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


def _save_paid_operation(monkeypatch, project_id: str, kind: str = "world") -> dict:
    """Run one job that pays for a draft and then fails on the 429 cap."""
    monkeypatch.setenv("WORLDLABS_RATE_LIMIT_MAX_ATTEMPTS", "2")
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
    job = db.create_job(project_id, kind)
    asyncio.run(pipeline.run_job(job.id))
    project = db.get_project(project_id)
    assert project.error == WORLD_RATE_LIMIT_EXHAUSTED
    assert json.loads(project.resume_operation)["operation_id"] == "paid-op-1"
    return generates


def _watch_resume(monkeypatch) -> list:
    polled = []

    async def poll(operation_id, timeout_s, on_tick=None):
        polled.append(operation_id)
        return {"world_id": None, "marble_url": None, "provider": "worldlabs", "operation_id": operation_id}

    async def cont(image, label, media_asset_id, **kwargs):
        return {"world_id": "full-2", "marble_url": "https://marble.worldlabs.ai/world/full-2",
                "provider": "worldlabs", "operation_id": "full-op"}

    monkeypatch.setattr(pipeline, "poll_world_operation", poll)
    monkeypatch.setattr(pipeline, "continue_full_world", cont)
    return polled


def _fresh_generate(monkeypatch) -> dict:
    calls = {"n": 0}

    async def generate(*args, **kwargs):
        calls["n"] += 1
        return {"world_id": "fresh-1", "marble_url": "https://marble.worldlabs.ai/world/fresh-1",
                "provider": "worldlabs", "operation_id": "fresh-op"}

    monkeypatch.setattr(pipeline, "generate_world", generate)
    return calls


def test_changed_panorama_does_not_resume_the_old_operation(client, monkeypatch):
    project_id = _ready_project("hash-mismatch")
    _save_paid_operation(monkeypatch, project_id)
    get_storage().put_bytes(panorama_key(project_id), b"\xff\xd8different", "image/jpeg")
    polled = _watch_resume(monkeypatch)
    fresh = _fresh_generate(monkeypatch)
    asyncio.run(pipeline.run_job(db.create_job(project_id, "world").id))
    project = db.get_project(project_id)
    assert polled == []
    assert fresh["n"] == 1
    assert project.resume_operation is None
    assert project.world_id == "fresh-1"


def test_upload_after_a_saved_operation_does_not_resume(client, monkeypatch):
    from tests.test_api import _jpeg

    project_id = client.post("/api/v1/projects", json={"name": "reupload"}).json()["id"]
    key = panorama_key(project_id)
    get_storage().put_bytes(key, b"\xff\xd8old", "image/jpeg")
    db.update_project(project_id, panorama_key=key, status="stitched")
    _save_paid_operation(monkeypatch, project_id)
    db.update_project(project_id, status="failed")
    polled = _watch_resume(monkeypatch)
    _fresh_generate(monkeypatch)
    response = client.post(
        f"/api/v1/projects/{project_id}/upload",
        files=[("images", (f"{i}.jpg", _jpeg(i), "image/jpeg")) for i in range(4)],
    )
    assert response.status_code == 200, response.text
    assert db.get_project(project_id).resume_operation is None
    assert polled == []


def test_restage_after_a_saved_operation_does_not_resume(client, monkeypatch):
    from storage import staged_key

    project_id = _ready_project("restage")
    skey = staged_key(project_id)
    get_storage().put_bytes(skey, b"\xff\xd8staged-old", "image/jpeg")
    db.update_project(project_id, staged_key=skey, staging_status="staged", staging_prompt="sofa")
    _save_paid_operation(monkeypatch, project_id, kind="staged_world")

    async def fake_stage(image_bytes, user_prompt, before_generate=None):
        return b"\xff\xd8staged-new", "gemini_mock", None

    monkeypatch.setattr(pipeline, "stage_image", fake_stage)
    asyncio.run(pipeline.run_job(db.create_job(project_id, "stage").id))
    assert db.get_project(project_id).resume_operation is None
    polled = _watch_resume(monkeypatch)
    fresh = _fresh_generate(monkeypatch)
    asyncio.run(pipeline.run_job(db.create_job(project_id, "staged_world").id))
    assert polled == []
    assert fresh["n"] == 1


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
    assert len(recorded["image_sha256"]) == 64  # same panorama: hash matches and it resumes
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


def test_delete_all_my_data(client):
    from storage import staged_key, upload_key

    token = client.raw.post("/api/v1/devices").json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    created = client.raw.post("/api/v1/projects", json={"name": "mine"}, headers=auth).json()
    project_id, share = created["id"], created["share_token"]
    storage = get_storage()
    keys = [upload_key(project_id, 0), panorama_key(project_id), staged_key(project_id)]
    for key in keys:
        storage.put_bytes(key, b"\xff\xd8x", "image/jpeg")
    db.update_project(project_id, panorama_key=keys[1], staged_key=keys[2], status="stitched")
    other = client.post("/api/v1/projects", json={"name": "not mine"}).json()["id"]
    assert db.get_by_share_token(share) is not None

    assert client.raw.delete("/api/v1/devices/me").status_code == 401
    assert client.raw.delete("/api/v1/devices/me", headers=auth).status_code == 204

    assert db.get_project(project_id) is None
    assert db.get_by_share_token(share) is None
    assert client.raw.get(f"/v/{share}").status_code == 404
    for key in keys:
        assert not storage.list_keys(key)
    assert db.get_project(other) is not None
    assert client.raw.get("/api/v1/projects", headers=auth).status_code == 401


def _device_project(name: str):
    device_id, token = db.create_device()
    project = db.create_project(name, owner_device_id=device_id)
    key = panorama_key(project.id)
    get_storage().put_bytes(key, b"\xff\xd8pano", "image/jpeg")
    db.update_project(project.id, panorama_key=key, status="stitched", staging_prompt="sofa")
    return device_id, project


@pytest.mark.parametrize("in_process", [True, False])
@pytest.mark.parametrize("kind", ["world", "stage"])
def test_job_finishing_after_delete_all_writes_nothing(client, monkeypatch, kind, in_process):
    from auth import Caller
    from routers import devices
    from storage import project_asset_prefixes, staged_key

    device_id, project = _device_project(f"late-{kind}-{in_process}")
    job = db.create_job(project.id, kind)
    release = asyncio.Event()
    finished = {"provider": False}
    deleted_worlds = []

    async def generate(*args, on_operation=None, **kwargs):
        await release.wait()
        finished["provider"] = True
        await on_operation("late-op", "full")
        return {"world_id": "late-world", "marble_url": "https://marble.worldlabs.ai/world/late-world",
                "provider": "worldlabs", "operation_id": "late-op"}

    async def stage(image_bytes, prompt, before_generate=None):
        await release.wait()
        finished["provider"] = True
        return b"\xff\xd8late-staged", "gemini", None

    async def record_deletes(ids):
        deleted_worlds.extend(ids)

    monkeypatch.setattr(pipeline, "generate_world", generate)
    monkeypatch.setattr(pipeline, "stage_image", stage)
    monkeypatch.setattr(pipeline, "delete_worlds", record_deletes)
    if not in_process:
        # Another replica runs the job: no local abort hook, only the write guards.
        monkeypatch.setattr(pipeline, "abort_local_jobs", lambda ids: 0)
        monkeypatch.setattr(devices, "abort_local_jobs", lambda ids: 0)

    async def scenario():
        task = asyncio.create_task(pipeline.run_job(job.id))
        for _ in range(200):
            current = db.get_job(job.id)
            if current is not None and current.lease_owner == db.WORKER_ID:
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        await devices.delete_all_my_data(Caller(device_id=device_id, is_admin=False))
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert db.get_project(project.id) is None
    assert db.get_job(job.id) is None
    storage = get_storage()
    for prefix in project_asset_prefixes(project.id):
        assert not storage.list_keys(prefix), prefix
    assert not storage.list_keys(staged_key(project.id))
    if not in_process:
        assert finished["provider"] is True


def test_delete_all_leaves_other_devices_and_demo_shares(client):
    _, mine = _device_project("mine-only")
    other_device, theirs = _device_project("theirs")
    demo = db.create_project("demo", owner_device_id=None)
    demo_key = panorama_key(demo.id)
    get_storage().put_bytes(demo_key, b"\xff\xd8demo", "image/jpeg")
    db.update_project(demo.id, panorama_key=demo_key, status="stitched")
    their_job = db.create_job(theirs.id, "world")
    token = client.raw.post("/api/v1/devices").json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.raw.delete("/api/v1/devices/me", headers=auth).status_code == 204
    try:
        for project, key in ((theirs, panorama_key(theirs.id)), (demo, demo_key)):
            kept = db.get_project(project.id)
            assert kept is not None
            assert kept.share_token == project.share_token
            assert db.get_by_share_token(project.share_token) is not None
            assert client.raw.get(f"/v/{project.share_token}").status_code == 200
            assert get_storage().list_keys(key)
        assert db.get_project(mine.id) is not None
        assert db.get_job(their_job.id).status == "pending"
        assert db.device_id_for_token(token) is None
    finally:
        db.delete_job(their_job.id)


@pytest.mark.parametrize("kind", ["world", "stage"])
def test_cancelled_job_writes_nothing_before_rows_are_deleted(client, monkeypatch, kind):
    """The window between cancelling jobs and deleting rows: a late result must not write."""
    from storage import staged_key

    device_id, project = _device_project(f"window-{kind}")
    job = db.create_job(project.id, kind)
    release = asyncio.Event()

    async def generate(*args, on_operation=None, **kwargs):
        await release.wait()
        return {"world_id": "late-world", "marble_url": "https://marble.worldlabs.ai/world/late-world",
                "provider": "worldlabs", "operation_id": "late-op"}

    async def stage(image_bytes, prompt, before_generate=None):
        await release.wait()
        return b"\xff\xd8late-staged", "gemini", None

    async def no_deletes(ids):
        return None

    monkeypatch.setattr(pipeline, "generate_world", generate)
    monkeypatch.setattr(pipeline, "stage_image", stage)
    monkeypatch.setattr(pipeline, "delete_worlds", no_deletes)

    async def scenario():
        task = asyncio.create_task(pipeline.run_job(job.id))
        for _ in range(200):
            current = db.get_job(job.id)
            if current is not None and current.lease_owner == db.WORKER_ID:
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        assert db.cancel_device_jobs(device_id) == [job.id]
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(scenario())
        saved = db.get_project(project.id)
        assert saved.world_id is None
        assert saved.status != "complete"
        assert saved.staging_status != "staged"
        assert not get_storage().list_keys(staged_key(project.id))
        assert db.get_job(job.id).status == "cancelled"
    finally:
        db.delete_job(job.id)
        db.delete_project(project.id)


class _FakeWorldLabs:
    """Stands in for httpx.AsyncClient in services.world_gen."""

    deleted: list[str] = []
    status = 200
    explode = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        if _FakeWorldLabs.explode:
            raise httpx.ConnectError("world labs down")
        return self

    async def __aexit__(self, *args):
        return False

    async def delete(self, url, headers=None):
        _FakeWorldLabs.deleted.append(url.rsplit("/", 1)[-1])
        return httpx.Response(_FakeWorldLabs.status)


def _device_with_worlds(client, name: str):
    token = client.raw.post("/api/v1/devices").json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    project_id = client.raw.post("/api/v1/projects", json={"name": name}, headers=auth).json()["id"]
    ids = {
        "world_id": f"{name}-full",
        "world_preview_id": f"{name}-draft",
        "staged_world_id": f"{name}-sfull",
        "staged_world_preview_id": f"{name}-sdraft",
    }
    db.update_project(project_id, **ids)
    return auth, project_id, set(ids.values())


@pytest.mark.parametrize("mode", ["ok", "http_error", "exception"])
def test_delete_all_my_data_deletes_worlds_best_effort(client, monkeypatch, mode):
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    monkeypatch.setattr("services.world_gen.httpx.AsyncClient", _FakeWorldLabs)
    _FakeWorldLabs.deleted = []
    _FakeWorldLabs.status = 500 if mode == "http_error" else 200
    _FakeWorldLabs.explode = mode == "exception"
    auth, project_id, world_ids = _device_with_worlds(client, f"wl{mode.replace('_', '')}")
    try:
        response = client.raw.delete("/api/v1/devices/me", headers=auth)
        assert response.status_code == 204
        assert db.get_project(project_id) is None
        assert client.raw.get("/api/v1/projects", headers=auth).status_code == 401
        pending = set(db.pending_world_delete_ids(force=True))
        if mode == "ok":
            assert set(_FakeWorldLabs.deleted) == world_ids
            assert not (world_ids & pending)
        elif mode == "http_error":
            assert set(_FakeWorldLabs.deleted) == world_ids
            assert world_ids <= pending
    finally:
        _FakeWorldLabs.explode = False
        for world_id in world_ids:
            db.forget_world_delete(world_id)


def _use_fake_world_labs(monkeypatch):
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    monkeypatch.setattr("services.world_gen.httpx.AsyncClient", _FakeWorldLabs)
    _FakeWorldLabs.deleted = []
    _FakeWorldLabs.status = 200
    _FakeWorldLabs.explode = False


def test_shared_world_is_kept_until_the_last_project_is_deleted(client, monkeypatch):
    _use_fake_world_labs(monkeypatch)
    first = client.post("/api/v1/projects", json={"name": "share-a"}).json()["id"]
    second = client.post("/api/v1/projects", json={"name": "share-b"}).json()["id"]
    db.update_project(first, world_id="shared-world", world_preview_id="only-a")
    db.update_project(second, staged_world_id="shared-world")

    assert client.delete(f"/api/v1/projects/{first}").status_code == 204
    assert _FakeWorldLabs.deleted == ["only-a"]
    assert db.get_project(second).staged_world_id == "shared-world"

    assert client.delete(f"/api/v1/projects/{second}").status_code == 204
    assert _FakeWorldLabs.deleted == ["only-a", "shared-world"]


def test_delete_all_my_data_keeps_worlds_used_by_other_and_demo_projects(client, monkeypatch):
    _use_fake_world_labs(monkeypatch)
    auth, mine, world_ids = _device_with_worlds(client, "guarded")
    demo = db.create_project("demo share", owner_device_id=None)
    db.update_project(demo.id, world_id="guarded-full")
    other = client.post("/api/v1/projects", json={"name": "other device"}).json()["id"]
    db.update_project(other, staged_world_preview_id="guarded-sdraft")
    try:
        assert client.raw.delete("/api/v1/devices/me", headers=auth).status_code == 204
        assert db.get_project(mine) is None
        assert set(_FakeWorldLabs.deleted) == {"guarded-draft", "guarded-sfull"}
        assert db.get_project(demo.id).world_id == "guarded-full"
        assert db.get_by_share_token(demo.share_token) is not None
        assert db.get_project(other).staged_world_preview_id == "guarded-sdraft"
    finally:
        db.delete_project(demo.id)
        db.delete_project(other)
