"""Job leases stop a second worker from billing World Labs or Gemini again.

Sam's harnesses started three world jobs with World Labs concurrency 2, or one
long stage job, then backdated ``updated_at`` and ran the maintenance reclaim
(``resume_durable_jobs(fresh_running=False)``). The waiting job was claimed
again. These tests keep that shape with fake providers and no paid calls.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import config
import db
import services.pipeline as pipeline
import services.world_gen as world_gen
from services.world_gen import (
    WORLD_RATE_LIMIT_EXHAUSTED,
    OperationInFlight,
    RateLimitExceeded,
    _send_retrying_rate_limit,
    bind_rate_limit_hook,
    reset_rate_limit_hook,
)
from storage import get_storage, panorama_key


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _backdate(job_id: str, seconds: int = 600) -> None:
    with db.session_scope() as session:
        row = session.get(db.JobRow, job_id)
        assert row is not None
        row.updated_at = _utcnow() - timedelta(seconds=seconds)


def _ready_project(name: str) -> str:
    project = db.create_project(name, owner_device_id=f"lease-{name}")
    key = panorama_key(project.id)
    get_storage().put_bytes(key, b"\xff\xd8fake", "image/jpeg")
    db.update_project(project.id, panorama_key=key, status="stitched")
    return project.id


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


async def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


async def _drain_runner() -> None:
    tasks = [task for task in list(pipeline.runner._tasks) if not task.done()]
    if tasks:
        await asyncio.gather(*tasks)


def test_claim_is_exclusive_until_the_lease_expires(client):
    project_id = db.create_project("exclusive", owner_device_id="lease-exclusive").id
    job = db.create_job(project_id, "world")
    try:
        first = db.claim_job_lease(job.id, "worker-a")
        assert first is not None
        assert first.lease_owner == "worker-a"
        assert first.status == "running"
        assert db.claim_job_lease(job.id, "worker-a") is not None
        assert db.claim_job_lease(job.id, "worker-b") is None
        assert db.get_job(job.id).lease_owner == "worker-a"
        db.update_job(job.id, lease_expires_at=_utcnow() - timedelta(seconds=1))
        taken = db.claim_job_lease(job.id, "worker-b")
        assert taken is not None
        assert taken.lease_owner == "worker-b"
    finally:
        db.delete_job(job.id)


def test_stale_updated_at_does_not_steal_a_live_lease(client, monkeypatch):
    project_id = _ready_project("held")
    job = db.create_job(project_id, "world")
    db.update_job(
        job.id,
        status="running",
        operation_id="op-held",
        operation_phase="full",
        lease_owner="other-worker",
        lease_expires_at=_utcnow() + timedelta(seconds=120),
    )
    _backdate(job.id)
    calls = {"generate": 0, "poll": 0}

    async def generate(*args, **kwargs):
        calls["generate"] += 1
        raise AssertionError("a live lease must not generate")

    async def poll(*args, **kwargs):
        calls["poll"] += 1
        raise AssertionError("a live lease must not be polled")

    monkeypatch.setattr(pipeline, "generate_world", generate)
    monkeypatch.setattr(pipeline, "poll_world_operation", poll)
    monkeypatch.setattr(pipeline, "continue_full_world", generate)

    assert asyncio.run(pipeline.resume_durable_jobs(fresh_running=False)) == 0
    held = db.get_job(job.id)
    assert held is not None
    assert held.lease_owner == "other-worker"
    assert calls == {"generate": 0, "poll": 0}

    db.update_job(job.id, lease_expires_at=_utcnow() - timedelta(seconds=5))

    async def poll_once(operation_id: str, timeout_s: int, on_tick=None) -> dict:
        calls["poll"] += 1
        assert operation_id == "op-held"
        return _world(operation_id, "world-held")

    monkeypatch.setattr(pipeline, "poll_world_operation", poll_once)
    assert asyncio.run(pipeline.resume_durable_jobs(fresh_running=False)) == 1
    body = db.get_project(project_id)
    assert body is not None
    assert body.status == "complete"
    assert calls == {"generate": 0, "poll": 1}
    assert db.get_job(job.id) is None


def test_overlapping_resumes_poll_a_reclaimed_operation_once(client, monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "0")
    project_id = _ready_project("overlap")
    job = db.create_job(project_id, "world")
    db.update_job(
        job.id,
        status="running",
        operation_id="op-overlap",
        operation_phase="full",
        lease_owner="dead-worker",
        lease_expires_at=_utcnow() - timedelta(seconds=30),
    )
    _backdate(job.id)
    release = asyncio.Event()
    started = asyncio.Event()
    calls = {"poll": 0, "generate": 0}

    async def poll(operation_id: str, timeout_s: int, on_tick=None) -> dict:
        calls["poll"] += 1
        started.set()
        await release.wait()
        return _world(operation_id, "world-overlap")

    async def generate(*args, **kwargs):
        calls["generate"] += 1
        raise AssertionError("reclaim must poll the saved operation")

    monkeypatch.setattr(pipeline, "poll_world_operation", poll)
    monkeypatch.setattr(pipeline, "generate_world", generate)
    monkeypatch.setattr(pipeline, "continue_full_world", generate)

    async def run() -> None:
        try:
            first = await pipeline.resume_durable_jobs(fresh_running=False)
            await asyncio.wait_for(started.wait(), timeout=3)
            second = await pipeline.resume_durable_jobs(fresh_running=False)
            assert first == 1
            assert second == 0
        finally:
            release.set()
            await _drain_runner()

    asyncio.run(run())
    assert calls == {"poll": 1, "generate": 0}
    finished = db.get_project(project_id)
    assert finished is not None and finished.status == "complete"
    assert db.get_job(job.id) is None


def test_waiting_world_job_is_charged_once(client, monkeypatch):
    """Three world jobs, concurrency 2. The waiter used to be reclaimed and billed again."""
    monkeypatch.setenv("JOBS_INLINE", "0")
    monkeypatch.setenv("WORLDLABS_CONCURRENCY", "2")
    monkeypatch.setenv("WORKER_CONCURRENCY", "3")

    def fast_settings():
        return replace(config.get_settings(), job_lease_heartbeat_seconds=0.05)

    monkeypatch.setattr(pipeline, "get_settings", fast_settings)
    renews = {"n": 0}
    real_renew = pipeline.renew_job_lease

    def count_renew(job_id: str, owner: str) -> bool:
        renews["n"] += 1
        return real_renew(job_id, owner)

    monkeypatch.setattr(pipeline, "renew_job_lease", count_renew)
    pipeline._world_semaphore = None
    pipeline.runner._semaphore = None

    charges: Counter[tuple[str, str]] = Counter()
    started: set[str] = set()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_generate(image, label, on_preview=None, on_media=None, on_operation=None, on_submitting=None, on_tick=None):
        started.add(label)
        if len(started) >= 2:
            entered.set()
        await release.wait()
        await on_submitting("draft")
        charges[(label, "draft")] += 1
        await on_operation(f"op-draft-{charges[(label, 'draft')]}", "draft")
        if on_preview is not None:
            await on_preview({"world_id": None, "provider": "worldlabs"})
        await on_submitting("full")
        charges[(label, "full")] += 1
        await on_operation(f"op-full-{charges[(label, 'full')]}", "full")
        return _world("op-done", "worlddone")

    async def fake_continue(image, label, media, on_operation=None, on_submitting=None, on_tick=None):
        await on_submitting("full")
        charges[(label, "full")] += 1
        await on_operation("op-full-dup", "full")
        return _world("op-dup", "worlddup")

    async def fake_poll(operation_id: str, timeout_s: int, on_tick=None) -> dict:
        return _world(operation_id, "worldpolled")

    monkeypatch.setattr(pipeline, "generate_world", fake_generate)
    monkeypatch.setattr(pipeline, "continue_full_world", fake_continue)
    monkeypatch.setattr(pipeline, "poll_world_operation", fake_poll)

    async def run() -> None:
        jobs = []
        try:
            for index in range(3):
                project_id = _ready_project(f"world-{index}")
                outcome, job, _retired = db.begin_job(project_id, "world")
                assert outcome == "queued" and job is not None
                jobs.append((project_id, job.id))
                await pipeline.start_job(job.id)
            await asyncio.wait_for(entered.wait(), timeout=3)

            def all_leased() -> bool:
                rows = [db.get_job(job_id) for _project_id, job_id in jobs]
                return len(started) == 2 and all(row is not None and row.lease_owner == db.WORKER_ID for row in rows)

            await _wait_until(all_leased)
            waiting = [(project_id, job_id) for project_id, job_id in jobs if project_id not in started]
            assert len(waiting) == 1
            _project_id, waiting_job = waiting[0]
            waiting_row = db.get_job(waiting_job)
            assert waiting_row is not None
            assert db._as_utc(waiting_row.lease_expires_at) > _utcnow()
            await asyncio.sleep(0.25)
            assert renews["n"] >= 1

            _backdate(waiting_job)
            pipeline._in_flight.discard(waiting_job)
            try:
                assert await pipeline.resume_durable_jobs(fresh_running=False) == 0
            finally:
                pipeline._in_flight.add(waiting_job)
            assert db.get_job(waiting_job).lease_owner == db.WORKER_ID

            db.update_job(waiting_job, lease_expires_at=_utcnow() - timedelta(seconds=5))
            assert await pipeline.resume_durable_jobs(fresh_running=False) == 0
        finally:
            release.set()
            await _drain_runner()
            pipeline._world_semaphore = None
            pipeline.runner._semaphore = None

        for project_id, _job_id in jobs:
            assert charges[(project_id, "draft")] == 1
            assert charges[(project_id, "full")] == 1
            project = db.get_project(project_id)
            assert project is not None and project.status == "complete"

    asyncio.run(run())


def test_waiting_stage_job_calls_gemini_once(client, monkeypatch):
    """A long stage run used to be reclaimed from a stale ``updated_at`` and staged twice."""
    monkeypatch.setenv("JOBS_INLINE", "0")
    pipeline.runner._semaphore = None
    calls = {"n": 0}
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_stage(image, prompt):
        calls["n"] += 1
        assert prompt == "boho"
        started.set()
        await release.wait()
        return b"\xff\xd8x", "gemini", None

    monkeypatch.setattr(pipeline, "stage_image", fake_stage)
    project_id = _ready_project("stage")
    outcome, job, _retired = db.begin_job(project_id, "stage", project_fields={"staging_prompt": "boho"})
    assert outcome == "queued" and job is not None

    async def run() -> None:
        try:
            await pipeline.start_job(job.id)
            await asyncio.wait_for(started.wait(), timeout=3)
            row = db.get_job(job.id)
            assert row is not None and row.lease_owner == db.WORKER_ID
            _backdate(job.id)
            pipeline._in_flight.discard(job.id)
            try:
                assert await pipeline.resume_durable_jobs(fresh_running=False) == 0
            finally:
                pipeline._in_flight.add(job.id)
            assert calls["n"] == 1
        finally:
            release.set()
            await _drain_runner()
            pipeline.runner._semaphore = None

    asyncio.run(run())
    project = db.get_project(project_id)
    assert calls["n"] == 1
    assert project is not None and project.staging_status == "staged"


def test_persistent_429s_fail_the_project_after_the_attempt_cap(client, monkeypatch):
    monkeypatch.setenv("WORLDLABS_RATE_LIMIT_MAX_ATTEMPTS", "3")
    project_id = _ready_project("capped")
    job = db.create_job(project_id, "world")
    calls = {"n": 0}

    async def generate(*args, **kwargs):
        calls["n"] += 1
        hook = world_gen._rate_limit_hook.get()
        assert hook is not None
        for _ in range(5):
            hook()
        raise AssertionError("the attempt cap should have failed the call")

    monkeypatch.setattr(pipeline, "generate_world", generate)
    asyncio.run(pipeline.run_job(job.id))
    project = db.get_project(project_id)
    assert calls["n"] == 1
    assert project is not None
    assert project.status == "failed"
    assert project.error == WORLD_RATE_LIMIT_EXHAUSTED
    assert project.status != "generating_3d"
    assert db.get_job(job.id) is None


def test_rate_limit_budget_survives_resume_and_then_fails(client, monkeypatch):
    monkeypatch.setenv("WORLDLABS_RATE_LIMIT_MAX_ATTEMPTS", "3")
    project_id = _ready_project("budget")
    job = db.create_job(project_id, "world")
    calls = {"generate": 0, "poll": 0, "full": 0}

    async def generate(*args, **kwargs):
        calls["generate"] += 1
        on_operation = kwargs.get("on_operation")
        if on_operation is not None:
            await on_operation("op-draft", "draft")
        hook = world_gen._rate_limit_hook.get()
        assert hook is not None
        hook()
        hook()
        raise OperationInFlight("op-draft", "draft", "HTTP 429: slow down")

    async def poll(*args, **kwargs):
        calls["poll"] += 1
        hook = world_gen._rate_limit_hook.get()
        assert hook is not None
        hook()
        raise AssertionError("the third 429 should fail before another poll completes")

    async def full(*args, **kwargs):
        calls["full"] += 1
        raise AssertionError("a capped job must not start the full world")

    monkeypatch.setattr(pipeline, "generate_world", generate)
    monkeypatch.setattr(pipeline, "poll_world_operation", poll)
    monkeypatch.setattr(pipeline, "continue_full_world", full)

    asyncio.run(pipeline.run_job(job.id))
    held = db.get_job(job.id)
    assert held is not None
    assert held.status == "pending"
    assert held.operation_id == "op-draft"
    assert held.rate_limit_attempts == 2
    assert held.lease_owner is None
    assert db.get_project(project_id).status == "generating_3d"

    asyncio.run(pipeline.run_job(job.id))
    project = db.get_project(project_id)
    assert calls == {"generate": 1, "poll": 1, "full": 0}
    assert project is not None
    assert project.status == "failed"
    assert project.error == WORLD_RATE_LIMIT_EXHAUSTED
    assert db.get_job(job.id) is None


def test_rate_limit_time_cap_fails_before_another_provider_call(client, monkeypatch):
    monkeypatch.setenv("WORLDLABS_RATE_LIMIT_MAX_SECONDS", "30")
    project_id = _ready_project("time-cap")
    job = db.create_job(project_id, "world")
    db.update_job(
        job.id,
        rate_limit_attempts=1,
        rate_limit_started_at=_utcnow() - timedelta(seconds=120),
    )
    calls = {"n": 0}

    async def generate(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("the time cap must not call World Labs again")

    monkeypatch.setattr(pipeline, "generate_world", generate)
    with pytest.raises(RateLimitExceeded):
        pipeline.note_world_rate_limit(job.id)
    saved = db.get_job(job.id)
    assert saved is not None and saved.rate_limit_attempts == 2

    asyncio.run(pipeline.run_job(job.id))
    project = db.get_project(project_id)
    assert calls["n"] == 0
    assert project is not None
    assert project.status == "failed"
    assert project.error == WORLD_RATE_LIMIT_EXHAUSTED
    assert db.get_job(job.id) is None


def test_retry_and_lease_env_settings_are_clamped(monkeypatch):
    monkeypatch.setenv("WORLDLABS_RETRY_ATTEMPTS", "100")
    monkeypatch.setenv("WORLDLABS_RETRY_BASE_SECONDS", "10000")
    monkeypatch.setenv("WORLDLABS_RETRY_MAX_SECONDS", "10000")
    monkeypatch.setenv("WORLDLABS_RATE_LIMIT_MAX_ATTEMPTS", "1000")
    monkeypatch.setenv("WORLDLABS_RATE_LIMIT_MAX_SECONDS", "100000")
    monkeypatch.setenv("JOB_LEASE_SECONDS", "9999")
    monkeypatch.setenv("JOB_LEASE_HEARTBEAT_SECONDS", "9999")
    settings = config.get_settings()
    assert settings.worldlabs_retry_attempts == 8
    assert settings.worldlabs_retry_base_seconds == 60
    assert settings.worldlabs_retry_max_seconds == 60
    assert settings.worldlabs_rate_limit_max_attempts == 40
    assert settings.worldlabs_rate_limit_max_seconds == 3600
    assert settings.job_lease_seconds == 600
    assert settings.job_lease_heartbeat_seconds == 200

    monkeypatch.setenv("JOB_LEASE_SECONDS", "30")
    monkeypatch.setenv("JOB_LEASE_HEARTBEAT_SECONDS", "1")
    tight = config.get_settings()
    assert tight.job_lease_seconds == 30
    assert tight.job_lease_heartbeat_seconds == 5


def test_each_429_counts_toward_the_job_cap(monkeypatch):
    monkeypatch.setenv("WORLDLABS_RETRY_ATTEMPTS", "8")
    monkeypatch.setattr(world_gen.asyncio, "sleep", lambda _seconds: _async_none())
    sends = {"n": 0}

    async def send():
        sends["n"] += 1
        return SimpleNamespace(status_code=429, headers={})

    seen = {"n": 0}

    def hook() -> None:
        seen["n"] += 1
        if seen["n"] >= 3:
            raise RateLimitExceeded("cap")

    token = bind_rate_limit_hook(hook)
    try:
        with pytest.raises(RateLimitExceeded):
            asyncio.run(_send_retrying_rate_limit(send))
    finally:
        reset_rate_limit_hook(token)
    assert sends["n"] == 3
    assert seen["n"] == 3


async def _async_none() -> None:
    return None
