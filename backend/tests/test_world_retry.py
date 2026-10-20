"""World Labs 429 backoff, and deleting both worlds when a tour is regenerated."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from services.world_gen import (
    WORLD_PROVIDER_QUOTA_MESSAGE,
    OperationInFlight,
    ProviderFailure,
    _generate_models,
    _retry_after_seconds,
    retry_wait_seconds,
)
import services.pipeline as pipeline
import services.world_gen as world_gen
from storage import get_storage, panorama_key


class _Response:
    def __init__(self, status_code: int, body: dict, headers: dict | None = None):
        self.status_code = status_code
        self._body = body
        self.text = str(body)
        self.headers = headers or {}

    def json(self):
        return self._body


def _done(operation_id: str, world_id: str) -> _Response:
    return _Response(
        200,
        {
            "operation_id": operation_id,
            "done": True,
            "response": {
                "world_id": world_id,
                "world_marble_url": f"https://marble.worldlabs.ai/world/{world_id}",
            },
        },
    )


def _rate_limit(retry_after: str = "2") -> _Response:
    return _Response(429, {"error": {"message": "slow down"}}, headers={"Retry-After": retry_after})


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


def test_retry_wait_backs_off_with_jitter_and_honours_retry_after(monkeypatch):
    monkeypatch.setenv("WORLDLABS_RETRY_BASE_SECONDS", "1")
    monkeypatch.setenv("WORLDLABS_RETRY_MAX_SECONDS", "30")
    monkeypatch.setattr(world_gen.random, "uniform", lambda _low, high: high)
    assert retry_wait_seconds(1, None) == 1
    assert retry_wait_seconds(2, None) == 2
    assert retry_wait_seconds(3, None) == 4
    assert retry_wait_seconds(10, None) == 30

    monkeypatch.setattr(world_gen.random, "uniform", lambda _low, _high: 0)
    assert retry_wait_seconds(1, 5) == 5
    assert retry_wait_seconds(3, 100) == 30


def test_retry_after_parses_seconds_and_http_dates():
    assert _retry_after_seconds(_Response(429, {}, headers={"Retry-After": "1.5"})) == 1.5
    assert _retry_after_seconds(_Response(429, {}, headers={"Retry-After": "0"})) == 0
    past = format_datetime(datetime.now(timezone.utc) - timedelta(minutes=5), usegmt=True)
    assert _retry_after_seconds(_Response(429, {}, headers={"Retry-After": past})) == 0
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10), usegmt=True)
    parsed = _retry_after_seconds(_Response(429, {}, headers={"Retry-After": future}))
    assert parsed is not None and 8 <= parsed <= 12
    assert _retry_after_seconds(_Response(429, {}, headers={"Retry-After": "not-a-date"})) is None


def test_generate_retries_429_with_retry_after_then_stops(monkeypatch):
    monkeypatch.setenv("WORLDLABS_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("WORLDLABS_RETRY_BASE_SECONDS", "1")
    monkeypatch.setenv("WORLDLABS_RETRY_MAX_SECONDS", "30")
    monkeypatch.setattr(world_gen.random, "uniform", lambda _low, _high: 0)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(world_gen.asyncio, "sleep", fake_sleep)
    client = _ScriptedClient([_rate_limit("2"), _rate_limit("2"), _done("op-full", "world-full")])
    result = asyncio.run(
        _generate_models(client, {}, "room", "media-1", ["marble-1.1"], False)
    )
    assert result["world_id"] == "world-full"
    assert client.posts == ["marble-1.1", "marble-1.1", "marble-1.1"]
    assert client.gets == []
    assert sleeps == [2.0, 2.0]


def test_exhausted_429_does_not_keep_calling(monkeypatch):
    monkeypatch.setenv("WORLDLABS_RETRY_ATTEMPTS", "3")
    monkeypatch.setattr(world_gen.random, "uniform", lambda _low, _high: 0)
    monkeypatch.setattr(world_gen.asyncio, "sleep", lambda _seconds: _async_none())
    client = _ScriptedClient([_rate_limit(), _rate_limit(), _rate_limit(), _done("op-extra", "world-extra")])
    with pytest.raises(ProviderFailure) as caught:
        asyncio.run(_generate_models(client, {}, "room", "media-1", ["marble-1.0-draft", "marble-1.1"], False))
    assert len(client.posts) == 3
    assert client.posts == ["marble-1.0-draft", "marble-1.0-draft", "marble-1.0-draft"]
    assert caught.value.status_code == 429
    assert caught.value.client_message == WORLD_PROVIDER_QUOTA_MESSAGE
    assert not isinstance(caught.value, OperationInFlight)


def test_charged_draft_is_polled_instead_of_generated_again(monkeypatch):
    monkeypatch.setenv("WORLDLABS_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("WORLDLABS_RETRY_MAX_SECONDS", "30")
    monkeypatch.setattr(world_gen.random, "uniform", lambda _low, _high: 0)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(world_gen.asyncio, "sleep", fake_sleep)
    accepted = _Response(200, {"operation_id": "op-draft", "done": False})
    client = _ScriptedClient(
        [accepted, _done("op-should-not-run", "world-full")],
        [_rate_limit("2"), _rate_limit("2"), _rate_limit("2")],
    )
    with pytest.raises(OperationInFlight) as caught:
        asyncio.run(
            _generate_models(client, {}, "room", "media-1", ["marble-1.0-draft", "marble-1.1"], False)
        )
    assert client.posts == ["marble-1.0-draft"]
    assert len(client.gets) == 3
    assert all(url.endswith("/operations/op-draft") for url in client.gets)
    assert sleeps == [2.0, 2.0]
    assert caught.value.operation_id == "op-draft"
    assert caught.value.phase == "draft"


def test_rate_limit_after_a_charged_draft_resumes_by_polling(client, monkeypatch):
    from db import create_job, get_job, update_project

    created = client.post("/api/v1/projects", json={"name": "Rate limited"}).json()
    project_id = created["id"]
    key = panorama_key(project_id)
    get_storage().put_bytes(key, b"panorama", "image/jpeg")
    update_project(project_id, status="stitched", panorama_key=key, active_job=None)
    job = create_job(project_id, "world")
    calls = {"generate": 0, "poll": 0, "full": 0}

    async def generate(*args, **kwargs):
        calls["generate"] += 1
        if calls["generate"] > 1:
            raise AssertionError("charged draft must not be submitted again")
        on_operation = kwargs.get("on_operation")
        if on_operation is not None:
            await on_operation("op-draft", "draft")
        raise OperationInFlight("op-draft", "draft", "HTTP 429: slow down")

    async def poll(operation_id: str, timeout_s: int, on_tick=None) -> dict:
        calls["poll"] += 1
        assert operation_id == "op-draft"
        return {
            "world_id": "world-draft",
            "marble_url": "https://marble.worldlabs.ai/world/world-draft",
            "provider": "worldlabs",
            "operation_id": operation_id,
        }

    async def full(*args, **kwargs):
        calls["full"] += 1
        on_operation = kwargs.get("on_operation")
        if on_operation is not None:
            await on_operation("op-full", "full")
        return {
            "world_id": "world-full",
            "marble_url": "https://marble.worldlabs.ai/world/world-full",
            "provider": "worldlabs",
            "operation_id": "op-full",
        }

    monkeypatch.setattr(pipeline, "generate_world", generate)
    monkeypatch.setattr(pipeline, "poll_world_operation", poll)
    monkeypatch.setattr(pipeline, "continue_full_world", full)

    asyncio.run(pipeline.run_world(project_id, job_id=job.id))
    held = get_job(job.id)
    assert held is not None
    assert held.status == "pending"
    assert held.operation_id == "op-draft"
    assert held.operation_phase == "draft"
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["status"] == "generating_3d"
    assert body["error"] is None

    asyncio.run(pipeline.run_job(job.id))
    finished = client.get(f"/api/v1/projects/{project_id}").json()
    assert calls == {"generate": 1, "poll": 1, "full": 1}
    assert finished["status"] == "complete"
    assert finished["world_ready"] is True
    from db import get_project

    record = get_project(project_id)
    assert record.world_marble_url.endswith("/world-full")
    assert record.world_preview_url.endswith("/world-draft")
    assert get_job(job.id) is None


def test_regenerate_deletes_the_old_full_and_draft_worlds(client, monkeypatch, caplog):
    from db import pending_world_delete_ids, update_project

    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    deleted: list[str] = []

    class _DeleteResponse:
        status_code = 204

        def json(self):
            return {}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def delete(self, url, headers=None):
            deleted.append(url)
            assert headers["WLT-Api-Key"] == "wl-test"
            return _DeleteResponse()

    async def generate(*args, **kwargs):
        return {
            "provider": "worldlabs",
            "world_id": "world-new",
            "marble_url": "https://marble.worldlabs.ai/world/world-new",
            "splat_url": None,
            "mesh_url": None,
            "thumbnail_url": None,
            "operation_id": "op-new",
        }

    monkeypatch.setattr(world_gen.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(pipeline, "generate_world", generate)

    created = client.post("/api/v1/projects", json={"name": "Regenerate"}).json()
    project_id = created["id"]
    key = panorama_key(project_id)
    get_storage().put_bytes(key, b"panorama", "image/jpeg")
    update_project(
        project_id,
        status="stitched",
        panorama_key=key,
        active_job=None,
        world_id="world-old",
        world_preview_id="draft-old",
        world_marble_url="https://marble.worldlabs.ai/world/world-old",
        world_preview_url="https://marble.worldlabs.ai/world/draft-old",
    )

    with caplog.at_level(logging.INFO, logger="services.world_gen"):
        regenerated = client.post(f"/api/v1/projects/{project_id}/generate-3d")
        assert regenerated.status_code == 200, regenerated.text
        # Queued during the request; the background retry pass deletes them.
        assert deleted == []
        assert {"world-old", "draft-old"} <= set(pending_world_delete_ids(force=True))
        asyncio.run(world_gen.retry_pending_world_deletes(force=True))
    assert {url.rsplit("/", 1)[-1] for url in deleted} == {"world-old", "draft-old"}
    assert "Deleted Marble world world-old" in caplog.text
    assert "Deleted Marble world draft-old" in caplog.text
    assert "world-old" not in pending_world_delete_ids(force=True)
    assert "draft-old" not in pending_world_delete_ids(force=True)
    body = client.get(f"/api/v1/projects/{project_id}").json()
    from db import get_project

    assert body["world_ready"] is True
    assert get_project(project_id).world_marble_url.endswith("/world-new")


async def _async_none() -> None:
    return None
