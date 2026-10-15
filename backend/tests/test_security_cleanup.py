"""Low-severity cleanup: NUL URLs, share limits, JSON headers, drafts, and credit caps."""

from __future__ import annotations

import asyncio

import pytest

from services.world_gen import (
    ProviderFailure,
    WORLDLABS_BASE,
    _generate_models,
    _poll_until_done,
    _upload_panorama,
)


class _Response:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = str(body)
        self.headers: dict = {}

    def json(self):
        return self._body


def _world(operation_id: str, world_id: str, *, done: bool = True) -> _Response:
    return _Response(
        200,
        {
            "operation_id": operation_id,
            "done": done,
            "response": {
                "world_id": world_id,
                "world_marble_url": f"https://marble.worldlabs.ai/world/{world_id}",
            },
        },
    )


class _ScriptedClient:
    def __init__(self, posts: list[_Response], gets: list[_Response] | None = None):
        self._posts = list(posts)
        self._gets = list(gets or [])
        self.posts: list[str] = []
        self.gets: list[str] = []

    async def post(self, url, headers=None, json=None):
        self.posts.append(json["model"])
        if not self._posts:
            raise AssertionError(f"unexpected worlds:generate for {json['model']}")
        return self._posts.pop(0)

    async def get(self, url, headers=None):
        self.gets.append(url)
        if not self._gets:
            raise AssertionError(f"unexpected poll {url}")
        return self._gets.pop(0)


def _json_headers(response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    policy = response.headers["content-security-policy"]
    assert policy.startswith("default-src 'none'")
    assert "frame-ancestors 'none'" in policy
    assert "base-uri 'none'" in policy


def test_nul_in_a_url_is_not_a_server_error(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/v1/projects/{missing}").status_code == 404
    assert client.get("/api/v1/projects/not-a-uuid").status_code == 404

    from main import app

    async def call(path: str, raw_path: bytes):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": raw_path,
                "query_string": b"",
                "headers": [],
                "client": ("203.0.113.8", 1234),
                "server": ("testserver", 80),
                "root_path": "",
            },
            receive,
            send,
        )
        return messages

    for path, raw in (
        ("/v/\x00", b"/v/%00"),
        ("/api/v1/projects/\x00", b"/api/v1/projects/%00"),
        ("/p/ok", b"/p/ok%00"),
    ):
        messages = asyncio.run(call(path, raw))
        start = next(item for item in messages if item["type"] == "http.response.start")
        assert start["status"] == 404


def test_share_pages_are_rate_limited_per_ip(client, monkeypatch):
    from ratelimit import limiter

    monkeypatch.setenv("SHARE_RATE_PER_MINUTE", "2")
    limiter._hits.pop("share:testclient", None)
    first = client.raw.get("/v/not-a-real-token")
    second = client.raw.get("/p/not-a-real-token")
    third = client.raw.get("/v/not-a-real-token")
    assert first.status_code == 404
    assert second.status_code == 404
    assert third.status_code == 429
    assert third.json()["detail"] == "Too many requests"
    limiter._hits.pop("share:testclient", None)


def test_json_responses_send_security_headers_and_share_pages_do_not_deny_frames(client):
    projects = client.get("/api/v1/projects")
    health = client.raw.get("/health")
    assert projects.status_code == 200
    assert health.status_code == 200
    _json_headers(projects)
    _json_headers(health)
    share = client.raw.get("/v/missing-token")
    assert share.status_code == 404
    assert "x-frame-options" not in share.headers
    assert "frame-ancestors 'none'" in share.headers["content-security-policy"]
    assert "img-src 'self'" in share.headers["content-security-policy"]


def test_orphaned_draft_world_is_queued_for_delete():
    from db import pending_draft_cleanup_ids, pending_world_delete_ids

    accepted = _Response(200, {"operation_id": "op-draft", "done": False})
    client = _ScriptedClient(
        [accepted, _world("op-full", "world-full")],
        [_Response(500, {"error": {"message": "draft poll failed"}}), _world("op-draft", "world-draft")],
    )
    result = asyncio.run(
        _generate_models(client, {}, "room", "media-1", ["marble-1.0-draft", "marble-1.1"], False)
    )
    assert result["world_id"] == "world-full"
    assert client.posts == ["marble-1.0-draft", "marble-1.1"]
    assert "world-draft" in pending_world_delete_ids(force=True)
    assert "op-draft" not in pending_draft_cleanup_ids()
    _clear_pending()


def test_unfinished_orphaned_draft_is_remembered_for_a_later_poll():
    from db import pending_draft_cleanup_ids

    accepted = _Response(200, {"operation_id": "op-live", "done": False})
    still_running = _Response(200, {"operation_id": "op-live", "done": False})
    client = _ScriptedClient(
        [accepted, _world("op-full", "world-full")],
        [_Response(500, {"error": {"message": "draft poll failed"}}), still_running],
    )
    result = asyncio.run(
        _generate_models(client, {}, "room", "media-1", ["marble-1.0-draft", "marble-1.1"], False)
    )
    assert result["world_id"] == "world-full"
    assert "op-live" in pending_draft_cleanup_ids()
    assert any(url.endswith("/operations/op-live") for url in client.gets)
    _clear_pending()


def test_upload_url_must_be_https_on_an_allowed_host():
    class _UploadClient:
        def __init__(self, upload_info: dict):
            self.upload_info = upload_info
            self.requested: list[tuple[str, str]] = []

        async def post(self, url, headers=None, json=None):
            return _Response(
                200,
                {"media_asset": {"media_asset_id": "media-1"}, "upload_info": self.upload_info},
            )

        async def request(self, method, url, content=None, headers=None):
            self.requested.append((method, url))
            return _Response(200, {})

    refused = _UploadClient({"upload_url": "http://evil.example/pano.jpg", "upload_method": "PUT"})
    with pytest.raises(ProviderFailure):
        asyncio.run(_upload_panorama(refused, {}, b"jpeg"))
    assert refused.requested == []

    foreign = _UploadClient({"upload_url": "https://evil.example/pano.jpg", "upload_method": "PUT"})
    with pytest.raises(ProviderFailure):
        asyncio.run(_upload_panorama(foreign, {}, b"jpeg"))
    assert foreign.requested == []

    wrong_method = _UploadClient(
        {"upload_url": "https://storage.googleapis.com/bucket/pano.jpg", "upload_method": "GET"}
    )
    with pytest.raises(ProviderFailure):
        asyncio.run(_upload_panorama(wrong_method, {}, b"jpeg"))
    assert wrong_method.requested == []

    allowed = _UploadClient(
        {"upload_url": "https://storage.googleapis.com/bucket/pano.jpg", "upload_method": "PUT"}
    )
    media_id = asyncio.run(_upload_panorama(allowed, {}, b"jpeg"))
    assert media_id == "media-1"
    assert allowed.requested == [("PUT", "https://storage.googleapis.com/bucket/pano.jpg")]


def test_operation_id_is_checked_before_the_poll_url():
    class _PollClient:
        def __init__(self):
            self.gets: list[str] = []

        async def get(self, url, headers=None):
            self.gets.append(url)
            return _world("nope", "world-1")

    client = _PollClient()
    with pytest.raises(ProviderFailure):
        asyncio.run(_poll_until_done(client, {}, "../evil", 5))
    assert client.gets == []
    assert WORLDLABS_BASE


def test_global_daily_credit_cap_reserves_draft_and_full(monkeypatch):
    from sqlalchemy import delete

    from config import get_settings
    from db import DailyUsageRow, GLOBAL_QUOTA_DEVICE, release_world_quota, session_scope
    from services.quota import consume_world_quota, reserved_world_credits

    monkeypatch.delenv("GLOBAL_DAILY_CREDITS_CAP", raising=False)
    assert get_settings().global_daily_credits_cap == 30000
    assert reserved_world_credits() == 1650

    with session_scope() as session:
        session.execute(delete(DailyUsageRow).where(DailyUsageRow.device_id == GLOBAL_QUOTA_DEVICE))
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    monkeypatch.setenv("WORLDLABS_JOBS_PER_DEVICE_PER_DAY", "10")
    monkeypatch.setenv("GLOBAL_DAILY_WORLDS_CAP", "100")
    monkeypatch.setenv("GLOBAL_DAILY_CREDITS_CAP", "1650")
    assert consume_world_quota("credit-cap-a") is True
    assert consume_world_quota("credit-cap-b") is False

    with session_scope() as session:
        row = session.get(DailyUsageRow, (GLOBAL_QUOTA_DEVICE, _today(), "wl_credits"))
        assert row is not None and row.count == 1650
        session.expire_all()
        release_world_quota(session, "credit-cap-a", _today())
        session.expire_all()
        released = session.get(DailyUsageRow, (GLOBAL_QUOTA_DEVICE, _today(), "wl_credits"))
        assert released is not None and released.count == 0


def test_zero_credit_cap_does_not_block(monkeypatch):
    from sqlalchemy import delete

    from db import DailyUsageRow, GLOBAL_QUOTA_DEVICE, session_scope
    from services.quota import consume_world_quota

    with session_scope() as session:
        session.execute(delete(DailyUsageRow).where(DailyUsageRow.device_id == GLOBAL_QUOTA_DEVICE))
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    monkeypatch.setenv("WORLDLABS_JOBS_PER_DEVICE_PER_DAY", "10")
    monkeypatch.setenv("GLOBAL_DAILY_WORLDS_CAP", "100")
    monkeypatch.setenv("GLOBAL_DAILY_CREDITS_CAP", "0")
    assert consume_world_quota("credit-off-a") is True
    assert consume_world_quota("credit-off-b") is True


def _clear_pending() -> None:
    from db import (
        forget_draft_cleanup,
        forget_world_delete,
        pending_draft_cleanup_ids,
        pending_world_delete_ids,
    )

    for operation_id in pending_draft_cleanup_ids():
        forget_draft_cleanup(operation_id)
    for world_id in pending_world_delete_ids(force=True):
        forget_world_delete(world_id)


def _today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()
