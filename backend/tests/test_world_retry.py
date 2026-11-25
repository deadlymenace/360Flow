"""Atlas 429 backoff, operation polling resume, and asset cleanup on regeneration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from services.atlas_client import AtlasRateLimit, _post_with_retry
from services.world_gen import (
    OperationInFlight,
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


def _rate_limit(retry_after: str = "2") -> _Response:
    return _Response(429, {"error": {"message": "slow down", "code": "RATE_LIMIT_EXCEEDED"}}, headers={"Retry-After": retry_after})


class _ScriptedPostClient:
    def __init__(self, posts: list[_Response]):
        self._posts = list(posts)
        self.calls: int = 0

    async def post(self, url, headers=None, json=None):
        self.calls += 1
        return self._posts.pop(0)


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


def test_atlas_post_retries_429_with_retry_after_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("services.atlas_client.asyncio.sleep", fake_sleep)
    client = _ScriptedPostClient([_rate_limit("2"), _rate_limit("2"), _Response(201, {"id": "op_ok"})])
    out = asyncio.run(
        _post_with_retry(
            client,
            "https://api.atlas.worldlabs.ai/api/v2/tasks:posedImages2Splats",
            {"WLT-Api-Key": "k"},
            {},
            context="splats",
            retry_attempts=3,
        )
    )
    assert out["id"] == "op_ok"
    assert client.calls == 3
    assert sleeps == [2.0, 2.0]


def test_atlas_post_exhausts_429_and_raises_rate_limit(monkeypatch):
    monkeypatch.setattr("services.atlas_client.asyncio.sleep", lambda _s: _async_none())
    client = _ScriptedPostClient([_rate_limit("0"), _rate_limit("0"), _rate_limit("0")])
    with pytest.raises(AtlasRateLimit) as caught:
        asyncio.run(
            _post_with_retry(
                client,
                "https://api.atlas.worldlabs.ai/api/v2/tasks:posedImages2Splats",
                {"WLT-Api-Key": "k"},
                {},
                context="splats",
                retry_attempts=3,
            )
        )
    assert caught.value.status_code == 429
    assert client.calls == 3


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
            "marble_url": None,
            "splat_url": "https://api.atlas.worldlabs.ai/assets/draft.spz",
            "provider": "atlas",
            "operation_id": operation_id,
        }

    async def full(*args, **kwargs):
        calls["full"] += 1
        on_operation = kwargs.get("on_operation")
        if on_operation is not None:
            await on_operation("op-full", "full")
        return {
            "world_id": "world-full",
            "marble_url": None,
            "splat_url": "https://api.atlas.worldlabs.ai/assets/full.spz",
            "provider": "atlas",
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
    assert get_job(job.id) is None


def test_regenerate_deletes_the_old_full_and_draft_worlds(client, monkeypatch, caplog):
    from db import pending_world_delete_ids, update_project

    monkeypatch.setenv("ATLAS_API_KEY", "atlas-test")
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
            assert headers["WLT-Api-Key"] == "atlas-test"
            return _DeleteResponse()

    async def generate(*args, **kwargs):
        return {
            "provider": "atlas",
            "world_id": "world-new",
            "marble_url": None,
            "splat_url": "https://api.atlas.worldlabs.ai/assets/world-new.spz",
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
    )

    with caplog.at_level(logging.INFO, logger="services.world_gen"):
        regenerated = client.post(f"/api/v1/projects/{project_id}/generate-3d")
        assert regenerated.status_code == 200, regenerated.text
        assert deleted == []
        assert {"world-old", "draft-old"} <= set(pending_world_delete_ids(force=True))
        asyncio.run(world_gen.retry_pending_world_deletes(force=True))
    assert {url.rsplit("/", 1)[-1] for url in deleted} == {"world-old", "draft-old"}
    assert "Deleted Atlas asset world-old" in caplog.text
    assert "Deleted Atlas asset draft-old" in caplog.text
    assert "world-old" not in pending_world_delete_ids(force=True)
    assert "draft-old" not in pending_world_delete_ids(force=True)
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["world_ready"] is True


async def _async_none() -> None:
    return None
