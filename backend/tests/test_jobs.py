"""Durable jobs, worker concurrency, and operator-only provider errors."""

from __future__ import annotations

import json

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
    assert settings.worldlabs_concurrency == 2


def test_worldlabs_jobs_are_capped_separately_from_workers(monkeypatch):
    monkeypatch.setenv("WORKER_CONCURRENCY", "3")
    monkeypatch.setenv("WORLDLABS_CONCURRENCY", "2")
    pipeline._world_semaphore = None
    current = {"n": 0, "peak": 0}

    async def hold() -> None:
        async with pipeline.world_limit():
            current["n"] += 1
            current["peak"] = max(current["peak"], current["n"])
            await asyncio.sleep(0.05)
            current["n"] -= 1

    async def run() -> None:
        await asyncio.gather(*(hold() for _ in range(4)))

    asyncio.run(run())
    assert pipeline.world_limit()._value == 2
    assert current["peak"] == 2
    runner = pipeline.JobRunner()
    assert runner._limit()._value == 3
    pipeline._world_semaphore = None


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

    async def poll(operation_id: str, timeout_s: int, on_tick=None) -> dict:
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
    assert body["world_ready"] is True
    assert "world_marble_url" not in body
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

    async def poll(operation_id: str, timeout_s: int, on_tick=None) -> dict:
        assert operation_id == "op-draft"
        return _world(operation_id, "world-draft")

    async def full(image, label, media_asset_id, on_operation=None, on_submitting=None, on_tick=None):
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
    assert body["world_ready"] is True
    assert body["world_draft_ready"] is False
    assert "marble.worldlabs.ai" not in json.dumps(body) and "cdn." not in json.dumps(body)


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
    assert finished["world_ready"] is True
    assert finished["error"] is None
    assert calls["n"] == 2


def test_delayed_tour_can_be_retried_or_cancelled(client, monkeypatch):
    created = client.post("/api/v1/projects", json={"name": "Wait"}).json()
    project_id = created["id"]
    key = panorama_key(project_id)
    get_storage().put_bytes(key, b"panorama", "image/jpeg")
    update_project(project_id, status="delayed", panorama_key=key, active_job="world", error=WORLD_DELAYED_MESSAGE)
    job = create_job(project_id, "world")
    update_job(job.id, status="delayed")

    async def generate(*args, **kwargs):
        return {
            "provider": "worldlabs",
            "world_id": "world-after-retry",
            "marble_url": "https://marble.worldlabs.ai/world/world-after-retry",
            "splat_url": None,
            "mesh_url": None,
            "thumbnail_url": None,
            "operation_id": "op-after",
        }

    monkeypatch.setattr(pipeline, "generate_world", generate)
    retried = client.post(f"/api/v1/projects/{project_id}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "complete"

    update_project(project_id, status="delayed", active_job="world", error=WORLD_DELAYED_MESSAGE, panorama_key=key)
    again = create_job(project_id, "world")
    update_job(again.id, status="delayed")
    cancelled = client.post(f"/api/v1/projects/{project_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "stitched"
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["status"] == "stitched"
    assert body["has_original"] is True
    removed = client.delete(f"/api/v1/projects/{project_id}")
    assert removed.status_code == 204


def test_cancelling_before_a_paid_call_refunds_the_world_quota(client, monkeypatch):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from db import GLOBAL_QUOTA_DEVICE, DailyUsageRow, JobRow, device_id_for_token, session_scope

    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    monkeypatch.setenv("WORLDLABS_JOBS_PER_DEVICE_PER_DAY", "1")
    monkeypatch.setenv("GLOBAL_DAILY_WORLDS_CAP", "100")
    token = client.raw.post("/api/v1/devices").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    owner = device_id_for_token(token)
    assert owner

    async def out_of_credits(*args, **kwargs):
        raise ProviderFailure(WORLD_CREDITS_MESSAGE, "out of credits (remaining 0, marble-1.0-draft needs 150)", 402)

    monkeypatch.setattr(pipeline, "generate_world", out_of_credits)

    def usage(device_id: str) -> int:
        day = datetime.now(timezone.utc).date().isoformat()
        with session_scope() as session:
            row = session.get(DailyUsageRow, (device_id, day, "worldlabs"))
            return 0 if row is None else row.count

    created = client.raw.post("/api/v1/projects", json={"name": "Refund"}, headers=headers).json()
    project_id = created["id"]
    key = panorama_key(project_id)
    get_storage().put_bytes(key, b"panorama", "image/jpeg")
    update_project(project_id, status="stitched", panorama_key=key, active_job=None)
    before_global = usage(GLOBAL_QUOTA_DEVICE)

    first = client.raw.post(f"/api/v1/projects/{project_id}/generate-3d", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "delayed"
    assert usage(owner) == 1
    assert usage(GLOBAL_QUOTA_DEVICE) == before_global + 1

    blocked = client.raw.post(f"/api/v1/projects/{project_id}/generate-3d", headers=headers)
    assert blocked.status_code == 429
    assert "Daily 3D limit" in blocked.json()["detail"]

    cancelled = client.raw.post(f"/api/v1/projects/{project_id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert usage(owner) == 0
    assert usage(GLOBAL_QUOTA_DEVICE) == before_global

    second = client.raw.post(f"/api/v1/projects/{project_id}/generate-3d", headers=headers)
    assert second.status_code == 200, second.text
    assert usage(owner) == 1
    with session_scope() as session:
        job = session.scalar(select(JobRow).where(JobRow.project_id == project_id, JobRow.status == "delayed"))
        assert job is not None and job.operation_id is None
        job.operation_id = "op-paid"
        job.operation_phase = "draft"
    paid_cancel = client.raw.post(f"/api/v1/projects/{project_id}/cancel", headers=headers)
    assert paid_cancel.status_code == 200, paid_cancel.text
    assert usage(owner) == 1
    assert usage(GLOBAL_QUOTA_DEVICE) == before_global + 1
    refused = client.raw.post(f"/api/v1/projects/{project_id}/generate-3d", headers=headers)
    assert refused.status_code == 429


def test_delayed_world_fails_after_the_wait_cap(client):
    from datetime import datetime, timedelta, timezone

    from services.world_gen import WORLD_DELAY_EXPIRED_MESSAGE

    created = client.post("/api/v1/projects", json={"name": "Too long"}).json()
    project_id = created["id"]
    update_project(project_id, status="delayed", active_job="world", error=WORLD_DELAYED_MESSAGE)
    job = create_job(project_id, "world")
    update_job(job.id, status="delayed", attempt_count=1, delayed_at=datetime.now(timezone.utc) - timedelta(hours=25))
    assert asyncio.run(pipeline.retry_delayed_worlds()) == 0
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["status"] == "failed"
    assert body["error"] == WORLD_DELAY_EXPIRED_MESSAGE
    assert "402" not in body["error"]


def test_orphan_transient_project_still_fails_on_startup(client):
    created = client.post("/api/v1/projects", json={"name": "Orphan"}).json()
    project_id = created["id"]
    update_project(project_id, status="stitching")
    assert fail_interrupted_jobs() >= 1
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["status"] == "failed"
    assert "restart" in body["error"]
