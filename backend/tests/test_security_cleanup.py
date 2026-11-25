"""Low-severity cleanup: NUL URLs, share limits, JSON headers, Atlas validation, and credit caps."""

from __future__ import annotations

import asyncio

import pytest

from services.atlas_client import AtlasError, valid_atlas_base_url
from services.world_gen import WORLDLABS_BASE
from services.world_urls import valid_upload_url, valid_world_id


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
    limiter._hits.pop("share-page:testclient", None)
    first = client.raw.get("/v/not-a-real-token")
    second = client.raw.get("/p/not-a-real-token")
    third = client.raw.get("/v/not-a-real-token")
    assert first.status_code == 404
    assert second.status_code == 404
    assert third.status_code == 429
    assert third.json()["detail"] == "Too many requests"
    limiter._hits.pop("share-page:testclient", None)


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


def test_atlas_urls_and_world_ids_must_be_safe():
    assert WORLDLABS_BASE == "https://api.atlas.worldlabs.ai/api/v2"
    assert valid_atlas_base_url("https://api.atlas.worldlabs.ai/api/v2/") == "https://api.atlas.worldlabs.ai/api/v2"
    with pytest.raises(ValueError):
        valid_atlas_base_url("http://evil.example/api/v2")
    assert valid_upload_url("http://evil.example/pano.jpg") is None
    assert valid_upload_url("https://evil.example/pano.jpg") is None
    assert valid_upload_url("https://storage.googleapis.com/bucket/pano.jpg") is not None
    assert valid_world_id("../evil") is None
    assert valid_world_id("op_01jexample") == "op_01jexample"


def test_global_daily_credit_cap_reserves_atlas_draft_credits(monkeypatch):
    from sqlalchemy import delete

    from config import get_settings
    from db import DailyUsageRow, GLOBAL_QUOTA_DEVICE, release_world_quota, session_scope
    from services.quota import consume_world_quota, reserved_world_credits

    monkeypatch.delenv("GLOBAL_DAILY_CREDITS_CAP", raising=False)
    assert get_settings().global_daily_credits_cap == 30000
    assert reserved_world_credits() == 520

    with session_scope() as session:
        session.execute(delete(DailyUsageRow).where(DailyUsageRow.device_id == GLOBAL_QUOTA_DEVICE))
    monkeypatch.setenv("ATLAS_API_KEY", "atlas-test")
    monkeypatch.setenv("WORLDLABS_JOBS_PER_DEVICE_PER_DAY", "10")
    monkeypatch.setenv("GLOBAL_DAILY_WORLDS_CAP", "100")
    monkeypatch.setenv("GLOBAL_DAILY_CREDITS_CAP", "520")
    assert consume_world_quota("credit-cap-a") is True
    assert consume_world_quota("credit-cap-b") is False

    with session_scope() as session:
        row = session.get(DailyUsageRow, (GLOBAL_QUOTA_DEVICE, _today(), "wl_credits"))
        assert row is not None and row.count == 520
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
    monkeypatch.setenv("ATLAS_API_KEY", "atlas-test")
    monkeypatch.setenv("WORLDLABS_JOBS_PER_DEVICE_PER_DAY", "10")
    monkeypatch.setenv("GLOBAL_DAILY_WORLDS_CAP", "100")
    monkeypatch.setenv("GLOBAL_DAILY_CREDITS_CAP", "0")
    assert consume_world_quota("credit-off-a") is True
    assert consume_world_quota("credit-off-b") is True


def _today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()
