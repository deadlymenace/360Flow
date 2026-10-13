"""Durable jobs, worker concurrency, and operator-only provider errors."""

from __future__ import annotations

import asyncio
import threading
import time

from db import create_job, fail_interrupted_jobs, update_job, update_project
import services.pipeline as pipeline
from services.world_gen import WORLD_CREDITS_MESSAGE, WORLD_DELAYED_MESSAGE, ProviderFailure, note_out_of_credits
from storage import get_storage, panorama_key


def test_worker_concurrency_defaults_to_three():
    from config import get_settings

    settings = get_settings()
    assert settings.worker_concurrency == 3
    assert settings.stitch_concurrency == 1


def test_runner_limit_follows_worker_concurrency(monkeypatch):
    monkeypatch.setenv("WORKER_CONCURRENCY", "4")
    runner = pipeline.JobRunner()
    assert runner._limit()._value == 4


def test_stitch_pool_serializes_cpu_work(monkeypatch):
    monkeypatch.setenv("STITCH_CONCURRENCY", "1")
    pipeline._stitch_pool = None
    current = {"n": 0, "peak": 0}
    lock = threading.Lock()

    def cpu() -> bool:
        with lock:
            current["n"] += 1
            current["peak"] = max(current["peak"], current["n"])
        time.sleep(0.05)
        with lock:
            current["n"] -= 1
        return True

    pool = pipeline.stitch_executor()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        await asyncio.gather(*(loop.run_in_executor(pool, cpu) for _ in range(3)))

    asyncio.run(run())
    assert pool._max_workers == 1
    assert current["peak"] == 1
    pool.shutdown(wait=True, cancel_futures=False)
    pipeline._stitch_pool = None


def _world(operation_id: str, world_id: str) -> dict:
    return {
        "world_id": world_id,
        "marble_url": f"https://marble.worldlabs.ai/world/{world_id}",
        "splat_url": None,
        "mesh_url": None,
        "thumbnail_url": None,
        "provider": "worldlabs",
        "operation_id": operation_id,
    }


def test_resume_repolls_world_operation_without_generating(client, monkeypatch):
    created = client.post("/api/v1/projects", json={"name": "In flight"}).json()
    project_id = created["id"]
    update_project(project_id, status="generating_3d", active_job="world")
    job = create_job(project_id, "world")
    update_job(job.id, status="running", operation_id="op-full", operation_phase="full")
    calls = {"generate": 0, "poll": []}

    async def poll(operation_id: str, timeout_s: int) -> dict:
        calls["poll"].append(operation_id)
        assert timeout_s > 0
        return _world(operation_id, "world-resumed")

    async def generate(*args, **kwargs):
        calls["generate"] += 1
        raise AssertionError("generate must not be called")

    monkeypatch.setattr(pipeline, "poll_world_operation", poll)
    monkeypatch.setattr(pipeline, "generate_world", generate)
    monkeypatch.setattr(pipeline, "continue_full_world", generate)

    assert asyncio.run(pipeline.resume_durable_jobs()) == 1
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["status"] == "complete"
    assert body["world_marble_url"].endswith("/world-resumed")
    assert calls["poll"] == ["op-full"]
    assert calls["generate"] == 0
    assert body["provider_error"] is None
    assert "provider_error" not in client.get(f"/api/v1/projects/{project_id}/status").json()


def test_resumed_draft_continues_to_the_full_model(client, monkeypatch):
    created = client.post("/api/v1/projects", json={"name": "Draft done"}).json()
    project_id = created["id"]
    key = panorama_key(project_id)
    get_storage().put_bytes(key, b"panorama", "image/jpeg")
    update_project(project_id, status="generating_3d", active_job="world", panorama_key=key)
    job = create_job(project_id, "world")
    update_job(job.id, status="running", operation_id="op-draft", operation_phase="draft", media_asset_id="media-1")
    calls = {"generate": 0, "full": 0}

    async def poll(operation_id: str, timeout_s: int) -> dict:
        assert operation_id == "op-draft"
        return _world(operation_id, "world-draft")

    async def full(image, label, media_asset_id, on_operation=None, on_submitting=None):
        calls["full"] += 1
        assert media_asset_id == "media-1"
        assert image == b"panorama"
        if on_operation is not None:
            await on_operation("op-full", "full")
        return _world("op-full", "world-full")

    async def generate(*args, **kwargs):
        calls["generate"] += 1
        raise AssertionError("draft must not be generated again")

    monkeypatch.setattr(pipeline, "poll_world_operation", poll)
    monkeypatch.setattr(pipeline, "continue_full_world", full)
    monkeypatch.setattr(pipeline, "generate_world", generate)

    asyncio.run(pipeline.resume_durable_jobs())
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert calls == {"generate": 0, "full": 1}
    assert body["status"] == "complete"
    assert body["world_marble_url"].endswith("/world-full")
    assert body["world_preview_url"].endswith("/world-draft")


def test_resume_does_not_recharge_without_an_operation_id(client, monkeypatch):
    created = client.post("/api/v1/projects", json={"name": "No operation"}).json()
    project_id = created["id"]
    update_project(project_id, status="generating_3d", active_job="world")
    job = create_job(project_id, "world")
    update_job(job.id, status="running", operation_phase="submitting")
    calls = {"generate": 0}

    async def generate(*args, **kwargs):
        calls["generate"] += 1
        raise AssertionError("must not generate")

    monkeypatch.setattr(pipeline, "generate_world", generate)
    monkeypatch.setattr(pipeline, "poll_world_operation", generate)
    monkeypatch.setattr(pipeline, "continue_full_world", generate)

    asyncio.run(pipeline.resume_durable_jobs())
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["status"] == "failed"
    assert body["error"] == "3D generation was interrupted. Retry from the app."
    assert "HTTP" not in body["error"]
    assert calls["generate"] == 0
    assert body["provider_error"] is None

    monkeypatch.setenv("API_KEY", "secret-key")
    admin = client.raw.get(
        f"/api/v1/projects/{project_id}",
        headers={"X-API-Key": "secret-key"},
    ).json()
    assert "not started again" in admin["provider_error"]
    assert admin["error"] == body["error"]


def test_out_of_credits_delays_the_tour_until_credits_return(client, monkeypatch):
    created = client.post("/api/v1/projects", json={"name": "Out of credits"}).json()
    project_id = created["id"]
    key = panorama_key(project_id)
    get_storage().put_bytes(key, b"panorama", "image/jpeg")
    update_project(project_id, status="stitched", panorama_key=key, active_job=None)
    calls = {"n": 0}

    async def generate(image, label, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderFailure(WORLD_CREDITS_MESSAGE, "out of credits (remaining 0, marble-1.1 needs 1500)", 402)
        assert label == project_id or str(label).startswith(project_id)
        return {
            "provider": "worldlabs",
            "world_id": "world-later",
            "marble_url": "https://marble.worldlabs.ai/world/world-later",
            "splat_url": None,
            "mesh_url": None,
            "thumbnail_url": None,
            "operation_id": "op-later",
        }

    monkeypatch.setattr(pipeline, "generate_world", generate)
    asyncio.run(pipeline.enqueue_job(project_id, "world"))

    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["status"] == "delayed"
    assert body["error"] == WORLD_DELAYED_MESSAGE
    assert body["has_original"] is True
    assert "402" not in body["error"]
    assert "out of credits" not in body["error"]
    assert body["provider_error"] is None
    status = client.get(f"/api/v1/projects/{project_id}/status").json()
    assert status["status"] == "delayed"
    assert status["error"] == WORLD_DELAYED_MESSAGE
    assert "provider_error" not in status
    page = client.get(f"/v/{body['share_token']}")
    assert page.status_code == 200
    assert "Your 3D tour is delayed" in page.text
    assert "finish it shortly" in page.text
    world_page = client.get(f"/v/{body['share_token']}/world")
    assert "Your 3D tour is delayed" in world_page.text
    assert "out of credits" not in world_page.text

    monkeypatch.setenv("API_KEY", "secret-key")
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    note_out_of_credits()
    admin = client.raw.get(
        f"/api/v1/projects/{project_id}",
        headers={"X-API-Key": "secret-key"},
    ).json()
    assert admin["provider_error"].startswith("out of credits")
    assert admin["error"] == WORLD_DELAYED_MESSAGE
    assert asyncio.run(pipeline.retry_delayed_worlds()) == 0
    assert client.get(f"/api/v1/projects/{project_id}").json()["status"] == "delayed"

    async def balance():
        return 20000.0

    monkeypatch.setattr(pipeline, "world_credits_balance", balance)
    assert asyncio.run(pipeline.retry_delayed_worlds()) == 1
    finished = client.get(f"/api/v1/projects/{project_id}").json()
    assert finished["status"] == "complete"
    assert finished["world_marble_url"].endswith("/world-later")
    assert finished["error"] is None
    assert calls["n"] == 2


def test_orphan_transient_project_still_fails_on_startup(client):
    created = client.post("/api/v1/projects", json={"name": "Orphan"}).json()
    project_id = created["id"]
    update_project(project_id, status="stitching")
    assert fail_interrupted_jobs() >= 1
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["status"] == "failed"
    assert "restart" in body["error"]
