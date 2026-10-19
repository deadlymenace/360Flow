"""Follow-up hardening: lease edge cases, paid-operation resume, HSTS, device delete."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import config
import db
import services.pipeline as pipeline
import services.staging as staging
import services.world_gen as world_gen
from services.world_gen import WORLD_RATE_LIMIT_EXHAUSTED, LeaseLost
from storage import get_storage, panorama_key


def _ready_project(name: str) -> str:
    project = db.create_project(name, owner_device_id=f"followup-{name}")
    key = panorama_key(project.id)
    get_storage().put_bytes(key, b"\xff\xd8fake", "image/jpeg")
    db.update_project(project.id, panorama_key=key, status="stitched")
    return project.id


def test_heartbeat_stops_the_job_when_the_lease_read_also_fails(client, monkeypatch, caplog):
    project_id = _ready_project("double-fault")
    job = db.create_job(project_id, "world")
    assert db.claim_job_lease(job.id, db.WORKER_ID) is not None

    def renew(job_id, owner):
        raise RuntimeError("renew failed")

    def read(job_id):
        raise RuntimeError("read failed")

    monkeypatch.setattr(pipeline, "renew_job_lease", renew)
    monkeypatch.setattr(pipeline, "_lease_still_held", read)
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: replace(config.get_settings(), job_lease_heartbeat_seconds=0.01)
    )
    lost = {"n": 0}

    async def run():
        stop = asyncio.Event()
        await asyncio.wait_for(
            pipeline._lease_heartbeat(job.id, stop, lambda: lost.__setitem__("n", lost["n"] + 1)), 2
        )

    try:
        asyncio.run(run())
        assert lost["n"] == 1
        assert "stopping the job" in caplog.text
    finally:
        db.delete_job(job.id)


def test_renewal_checks_owner_and_expiry(client):
    project_id = _ready_project("renew-owner")
    job = db.create_job(project_id, "world")
    assert db.claim_job_lease(job.id, db.WORKER_ID) is not None
    try:
        assert db.renew_job_lease(job.id, "someone-else") is False
        assert db.renew_job_lease(job.id, db.WORKER_ID) is True
        db.update_job(job.id, lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        assert db.renew_job_lease(job.id, db.WORKER_ID) is False
    finally:
        db.delete_job(job.id)


def test_lost_lease_at_draft_does_not_continue_to_full(monkeypatch):
    phases = []

    async def fake_generate_model(client, headers, name, media, model, public, timeout_s, *, phase, **kwargs):
        phases.append(phase)
        if phase == "draft":
            raise LeaseLost()
        return {"world_id": "w"}

    monkeypatch.setattr(world_gen, "_generate_model", fake_generate_model)

    async def run():
        async with httpx.AsyncClient() as client:
            await world_gen._generate_models(client, {}, "n", "m", ["draft-model", "full-model"], False)

    with pytest.raises(LeaseLost):
        asyncio.run(run())
    assert phases == ["draft"]


def test_gemini_text_only_retry_rechecks_the_lease(monkeypatch):
    posts = {"n": 0}

    def handler(request):
        posts["n"] += 1
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "no image"}]}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        staging.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    checks = {"n": 0}

    def before_generate():
        checks["n"] += 1
        raise LeaseLost()

    import numpy as np

    image = np.zeros((8, 8, 3), dtype=np.uint8)
    with pytest.raises(LeaseLost):
        asyncio.run(staging._gemini_edit("k", "m", "i", {"text": "s"}, image, "1K", before_generate=before_generate))
    assert posts["n"] == 1
    assert checks["n"] == 1


def test_rate_limit_failure_resumes_the_paid_operation(client, monkeypatch):
    monkeypatch.setenv("WORLDLABS_RATE_LIMIT_MAX_ATTEMPTS", "2")
    project_id = _ready_project("paid-op")
    generates = {"n": 0}

    async def generate(*args, on_submitting=None, on_operation=None, on_media=None, **kwargs):
        generates["n"] += 1
        await on_media("media-1")
        await on_submitting("draft")
        await on_operation("paid-op-1", "draft")
        hook = world_gen._rate_limit_hook.get()
        for _ in range(5):
            hook()
        raise AssertionError("cap should have raised")

    monkeypatch.setattr(pipeline, "generate_world", generate)
    job = db.create_job(project_id, "world")
    asyncio.run(pipeline.run_job(job.id))
    project = db.get_project(project_id)
    assert project.status == "failed"
    assert project.error == WORLD_RATE_LIMIT_EXHAUSTED
    recorded = json.loads(project.resume_operation)
    assert recorded["operation_id"] == "paid-op-1"
    assert recorded["phase"] == "draft"

    polled, continued = [], []

    async def poll(operation_id, timeout_s, on_tick=None):
        polled.append(operation_id)
        return {"world_id": None, "marble_url": None, "provider": "worldlabs", "operation_id": operation_id}

    async def cont(image, label, media_asset_id, **kwargs):
        continued.append(media_asset_id)
        return {
            "world_id": "full-1",
            "marble_url": "https://marble.worldlabs.ai/world/full-1",
            "provider": "worldlabs",
            "operation_id": "full-op",
        }

    monkeypatch.setattr(pipeline, "poll_world_operation", poll)
    monkeypatch.setattr(pipeline, "continue_full_world", cont)
    retry = db.create_job(project_id, "world")
    asyncio.run(pipeline.run_job(retry.id))
    project = db.get_project(project_id)
    assert generates["n"] == 1
    assert polled == ["paid-op-1"]
    assert continued == ["media-1"]
    assert project.status == "complete"
    assert project.resume_operation is None


def test_api_responses_send_hsts(client):
    for response in (client.raw.get("/health"), client.get("/api/v1/projects"), client.raw.get("/nope")):
        assert "max-age=" in response.headers.get("strict-transport-security", "")


def test_device_can_delete_itself(client):
    token = client.raw.post("/api/v1/devices").json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.raw.delete("/api/v1/devices/me").status_code == 401
    assert client.raw.delete("/api/v1/devices/me", headers=auth).status_code == 204
    assert client.raw.get("/api/v1/projects", headers=auth).status_code == 401
