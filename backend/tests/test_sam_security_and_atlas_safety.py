"""Tests for Sam's security blockers, nits, opt-in 3D CLI, and Atlas live-divergence safety."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

import db
from services import atlas_client, byok, pipeline, world_assets, world_gen
from services.world_gen import ProviderFailure
from storage import get_storage, panorama_key


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "agensi-skill"
CLI_PATH = SKILL_DIR / "scripts" / "flow360.py"


def _pano_bytes(width: int = 256, height: int = 128) -> bytes:
    arr = np.full((height, width, 3), 120, dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (100, 120, 140)).save(buf, format="JPEG")
    return buf.getvalue()


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("flow360_cli_test", CLI_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_skill_package_size_and_untested_beta_labels():
    assert CLI_PATH.is_file()
    assert CLI_PATH.stat().st_size < 20480
    for name in ("SKILL.md", "README.md", "LISTING.md"):
        text = (SKILL_DIR / name).read_text().lower()
        assert "untested beta" in text, f"{name} must label 3D as untested beta"
    skill_text = (SKILL_DIR / "SKILL.md").read_text()
    assert "generativelanguage.googleapis.com" in skill_text
    assert "api.atlas.worldlabs.ai" in skill_text
    import re
    listing_text = (SKILL_DIR / "LISTING.md").read_text()
    urls = re.findall(r"https?://[^\s\"'`>]+", listing_text)
    assert all(u.startswith("https://api.example.com/") for u in urls), urls


def test_cli_check_keys_3d_is_opt_in_by_default(tmp_path):
    base_env = {
        "PATH": os.environ.get("PATH", ""),
        "FLOW360_API_URL": "https://agensi-prod-production.up.railway.app",
        "FLOW360_CONFIG_DIR": str(tmp_path / "cfg"),
    }
    # Default: panorama-only requires only FLOW360_API_URL
    r1 = subprocess.run([sys.executable, str(CLI_PATH), "check-keys"], env=base_env, capture_output=True, text=True)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert json.loads(r1.stdout)["ok"] is True

    # Opt-in --3d requires ATLAS_API_KEY
    r2 = subprocess.run([sys.executable, str(CLI_PATH), "check-keys", "--3d"], env=base_env, capture_output=True, text=True)
    assert r2.returncode == 2
    assert any("ATLAS_API_KEY" in m for m in json.loads(r2.stdout)["missing_required"])

    # With ATLAS_API_KEY + --3d succeeds
    r3 = subprocess.run(
        [sys.executable, str(CLI_PATH), "check-keys", "--3d"],
        env={**base_env, "ATLAS_API_KEY": "atl-buyer-test"},
        capture_output=True,
        text=True,
    )
    assert r3.returncode == 0, r3.stdout + r3.stderr


def test_cli_run_default_panorama_only_and_staging_plus_3d_no_409(tmp_path, monkeypatch):
    cli = _load_cli_module()
    cfg_dir = tmp_path / "cfg"
    p1 = tmp_path / "01.jpg"
    p2 = tmp_path / "02.jpg"
    p1.write_bytes(_jpeg_bytes())
    p2.write_bytes(_jpeg_bytes())

    calls: list[tuple[str, str, dict]] = []
    state = {"status": "stitched", "has_original": True, "staging_status": None, "has_staged": False}

    def fake_req(method, url, *, hdr=None, body=None, to=60.0, sec=None):
        calls.append((method, url, dict(hdr or {})))
        if method == "POST" and url.endswith("/api/v1/devices"):
            return 200, {"token": "tok-persisted-123"}
        if method == "POST" and url.endswith("/api/v1/projects"):
            return 200, {"id": "11111111-1111-1111-1111-111111111111", "name": "Room"}
        if "/upload?" in url:
            if "auto_3d=true" in url:
                state["status"] = "generating_3d"
            else:
                state["status"] = "stitched"
            return 200, {"status": state["status"], "image_count": 2}
        if method == "POST" and url.endswith("/stage"):
            if state["status"] == "generating_3d":
                raise RuntimeError("API HTTP 409: busy")
            state["staging_status"] = "staged"
            state["has_staged"] = True
            return 200, {"status": "staging"}
        if method == "GET" and url.endswith("/stage/status"):
            return 200, {"status": state["staging_status"] or "idle", "has_staged": state["has_staged"]}
        if method == "POST" and "/generate-3d?" in url:
            state["status"] = "complete"
            return 200, {"status": "generating_3d"}
        if method == "GET" and "/api/v1/projects/" in url:
            return 200, {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "Room",
                "status": state["status"],
                "image_count": 2,
                "has_original": state["has_original"],
                "has_staged": state["has_staged"],
                "world_ready": state["status"] == "complete",
                "share_url": "https://prod.example/v/abc",
            }
        raise AssertionError(f"Unexpected call: {method} {url}")

    monkeypatch.setattr(cli, "_req", fake_req)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    monkeypatch.setenv("FLOW360_API_URL", "https://prod.example")
    monkeypatch.setenv("FLOW360_CONFIG_DIR", str(cfg_dir))
    monkeypatch.delenv("FLOW360_API_KEY", raising=False)
    monkeypatch.delenv("FLOW360_DEVICE_TOKEN", raising=False)

    # 1. Default run is panorama-only (auto_3d=false, no ATLAS_API_KEY needed)
    monkeypatch.setattr(sys, "argv", ["flow360.py", "run", "--name", "R1", "--files", str(p1), str(p2)])
    assert cli.main() == 0
    upload_call = [c for c in calls if "/upload?" in c[1]][0]
    assert "auto_3d=false" in upload_call[1]
    assert "X-Atlas-Key" not in upload_call[2]
    # Device token saved locally with 0600 permissions
    tok_file = cfg_dir / "device.json"
    assert tok_file.is_file()
    assert (tok_file.stat().st_mode & 0o777) == 0o600

    # Follow-up status command reuses persisted token cleanly
    calls.clear()
    monkeypatch.setattr(sys, "argv", ["flow360.py", "status", "--project-id", "11111111-1111-1111-1111-111111111111"])
    assert cli.main() == 0
    assert calls[0][2]["Authorization"] == "Bearer tok-persisted-123"

    # 2. --stage + --3d uploads with auto_3d=false, stages first, then triggers generate-3d (no 409)
    calls.clear()
    state.update({"status": "stitched", "has_original": True, "staging_status": None, "has_staged": False})
    monkeypatch.setenv("GEMINI_API_KEY", "gem-buyer")
    monkeypatch.setenv("ATLAS_API_KEY", "atl-buyer")
    monkeypatch.setattr(
        sys,
        "argv",
        ["flow360.py", "run", "--name", "R2", "--stage", "modern sofa", "--3d", "--files", str(p1), str(p2)],
    )
    assert cli.main() == 0
    seq = [u.split("/api/v1/projects/")[1] for _m, u, _h in calls if "/api/v1/projects/" in u]
    up_idx = next(i for i, s in enumerate(seq) if "/upload?" in s)
    st_idx = next(i for i, s in enumerate(seq) if s.endswith("/stage"))
    g3_idx = next(i for i, s in enumerate(seq) if "/generate-3d?" in s)
    assert "auto_3d=false" in seq[up_idx]
    assert up_idx < st_idx < g3_idx


def test_byok_key_retained_across_429_resume_and_accepted_on_retry(client, monkeypatch):
    monkeypatch.setenv("BYOK_REQUIRED", "true")
    created = client.post("/api/v1/projects", json={"name": "BYOK Resume"}).json()
    pid = created["id"]
    key = panorama_key(pid)
    get_storage().put_bytes(key, _pano_bytes(), "image/jpeg")
    db.update_project(pid, status="stitched", panorama_key=key, active_job=None)
    job = db.create_job(pid, "world")
    byok.set_project_world_key(pid, "atl-live-retained")

    async def fake_gen(*args, **kwargs):
        assert kwargs.get("api_key") == "atl-live-retained"
        if kwargs.get("on_submitting"):
            await kwargs["on_submitting"]("draft")
        if kwargs.get("on_operation"):
            await kwargs["on_operation"]("op-held-429", "draft")
        raise ProviderFailure("Rate limited", "HTTP 429", 429)

    monkeypatch.setattr(pipeline, "generate_world", fake_gen)
    asyncio.run(pipeline.run_job(job.id))
    assert db.get_job(job.id).status == "pending"
    # Key MUST still be in memory while waiting to poll
    assert byok.get_project_world_key(pid) == "atl-live-retained"

    async def fake_poll(op_id, timeout_s, on_tick=None, *, api_key=None, require_byok=False):
        assert op_id == "op-held-429"
        assert api_key == "atl-live-retained"
        assert require_byok is True
        return {"provider": "atlas", "world_id": op_id, "splat_url": "https://cdn.example.com/ok.spz", "operation_id": op_id}

    async def fake_full(*args, **kwargs):
        assert kwargs.get("api_key") == "atl-live-retained"
        return {"provider": "atlas", "world_id": "op-full-done", "splat_url": "https://cdn.example.com/full.spz", "operation_id": "op-full-done"}

    monkeypatch.setattr(pipeline, "poll_world_operation", fake_poll)
    monkeypatch.setattr(pipeline, "continue_full_world", fake_full)
    monkeypatch.setattr(pipeline, "cache_world_assets", lambda *a, **kw: _async_none())

    asyncio.run(pipeline.run_job(job.id))
    assert db.get_project(pid).status == "complete"
    # Scrubbed only after completion
    assert byok.get_project_world_key(pid) is None

    # Missing key on /retry must return 400; providing X-Atlas-Key succeeds
    db.update_project(pid, status="failed", active_job="world", error="boom")
    no_key_retry = client.post(f"/api/v1/projects/{pid}/retry")
    assert no_key_retry.status_code == 400
    assert "X-Atlas-Key" in no_key_retry.text

    seen = {}

    async def fake_gen_retry(*args, **kwargs):
        seen["api_key"] = kwargs.get("api_key")
        return {"provider": "atlas", "world_id": "op-retried", "splat_url": "https://cdn.example.com/r.spz", "operation_id": "op-retried"}

    monkeypatch.setattr(pipeline, "generate_world", fake_gen_retry)
    ok_retry = client.post(f"/api/v1/projects/{pid}/retry", headers={"X-Atlas-Key": "atl-retry-key"})
    assert ok_retry.status_code == 200, ok_retry.text
    assert seen["api_key"] == "atl-retry-key"
    assert byok.get_project_world_key(pid) is None


def test_missing_byok_key_on_restart_fails_cleanly_with_resend_message(client, monkeypatch):
    monkeypatch.setenv("BYOK_REQUIRED", "true")
    created = client.post("/api/v1/projects", json={"name": "Restart Lost Key"}).json()
    pid = created["id"]
    key = panorama_key(pid)
    get_storage().put_bytes(key, _pano_bytes(), "image/jpeg")
    db.update_project(pid, status="generating_3d", panorama_key=key, active_job="world", staging_prompt="sofa")
    byok.clear_project_keys(pid)

    # 1. Missing Atlas key on _run_world
    job = db.create_job(pid, "world")
    asyncio.run(pipeline.run_job(job.id))
    p = db.get_project(pid)
    assert p.status == "failed"
    assert "resend" in (p.error or "").lower()

    # 2. Missing Atlas key on _resume_world
    job_r = db.create_job(pid, "world")
    db.update_job(job_r.id, operation_id="op-existing", operation_phase="full")
    db.update_project(pid, status="generating_3d", active_job="world")
    asyncio.run(pipeline.run_job(job_r.id))
    p = db.get_project(pid)
    assert p.status == "failed"
    assert "resend" in (p.error or "").lower()

    # 3. Missing Gemini key on run_stage
    asyncio.run(pipeline.run_stage(pid))
    p = db.get_project(pid)
    assert p.staging_status == "failed"
    assert "resend" in (p.staging_error or "").lower()


def test_cache_world_assets_passes_api_key_only_to_worldlabs_hosts(client, monkeypatch):
    created = client.post("/api/v1/projects", json={"name": "Cache Key"}).json()
    pid = created["id"]
    sent_headers: list[tuple[str, dict]] = []

    class _FakeResp:
        status_code = 200
        headers = {"content-length": "11"}

        def __init__(self, url: str):
            self.url = url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_bytes(self, chunk_size=65536):
            yield b"spz-payload"

    class _FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, headers=None):
            sent_headers.append((url, dict(headers or {})))
            return _FakeResp(url)

    monkeypatch.setattr(world_assets.httpx, "AsyncClient", _FakeClient)
    asyncio.run(
        world_assets.cache_world_assets(
            pid,
            "original",
            "https://api.atlas.worldlabs.ai/api/v2/assets/a1/spz",
            "https://storage.googleapis.com/signed-bucket/mesh.glb",
            api_key="atl-cache-secret",
        )
    )
    atlas_hdrs = [h for u, h in sent_headers if "worldlabs.ai" in u][0]
    gcs_hdrs = [h for u, h in sent_headers if "storage.googleapis.com" in u][0]
    assert atlas_hdrs.get("WLT-Api-Key") == "atl-cache-secret"
    assert "WLT-Api-Key" not in gcs_hdrs


def test_atlas_unexpected_response_and_4xx_refine_false_fail_cleanly(client, monkeypatch):
    created = client.post("/api/v1/projects", json={"name": "Atlas Divergence"}).json()
    pid = created["id"]
    pano = _pano_bytes()
    key = panorama_key(pid)
    get_storage().put_bytes(key, pano, "image/jpeg")

    class _MockResp:
        def __init__(self, status_code: int, payload, raw_text: str | None = None):
            self.status_code = status_code
            self._payload = payload
            self.headers = {}
            self.text = raw_text or ""

        def json(self):
            if self._payload is None:
                raise ValueError("not json")
            return self._payload

    # Case A: 400 on refine:false
    class _Client400:
        def __init__(self, **kw):
            self.asset_n = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            if url.endswith("/assets"):
                self.asset_n += 1
                return _MockResp(200, {"id": f"a_{self.asset_n}"})
            if url.endswith("/tasks:images2PosedRGBD"):
                frames = [
                    {
                        "imageAsset": {"assetId": f"a_{i+1}"},
                        "camera": {"extrinsics": {"position": [0, 0, 0], "quaternion": [0, 0, 0, 1]}, "intrinsics": {"fx": 640, "fy": 640, "cx": 640, "cy": 360, "width": 1280, "height": 720}},
                        "depth": {"depthAsset": {"assetId": f"d_{i+1}"}},
                    }
                    for i in range(10)
                ]
                return _MockResp(200, {"id": "op-pose", "done": True, "response": {"frames": frames}})
            if url.endswith("/tasks:posedImages2Splats"):
                assert json.get("refine") is False
                return _MockResp(400, {"error": {"code": "INVALID_ARGUMENT", "message": "Unknown field 'refine'"}})
            raise AssertionError(url)

    monkeypatch.setattr(world_gen.httpx, "AsyncClient", _Client400)
    db.update_project(pid, status="stitched", panorama_key=key, active_job=None)
    job = db.create_job(pid, "world")
    byok.set_project_world_key(pid, "atl-test-400")
    asyncio.run(pipeline.run_job(job.id))
    proj = db.get_project(pid)
    assert proj.status == "failed"
    assert proj.error is not None and "Unknown field 'refine'" in (proj.provider_error or "")

    # Case B: Missing response.frames field on images2PosedRGBD
    class _ClientMissingFrames(_Client400):
        async def post(self, url, headers=None, json=None):
            if url.endswith("/assets"):
                self.asset_n += 1
                return _MockResp(200, {"id": f"a_{self.asset_n}"})
            if url.endswith("/tasks:images2PosedRGBD"):
                return _MockResp(200, {"id": "op-pose-bad", "done": True, "response": {"unexpected": 1}})
            raise AssertionError(url)

    monkeypatch.setattr(world_gen.httpx, "AsyncClient", _ClientMissingFrames)
    db.update_project(pid, status="stitched", panorama_key=key, active_job=None)
    job2 = db.create_job(pid, "world")
    byok.set_project_world_key(pid, "atl-test-missing")
    asyncio.run(pipeline.run_job(job2.id))
    proj2 = db.get_project(pid)
    assert proj2.status == "failed"
    assert proj2.status != "generating_3d"


def test_no_duplicate_atlas_task_submission_and_deterministic_idempotency_key():
    pano = _pano_bytes()
    runs_headers: list[dict[str, str]] = []
    run_idx = 0

    class _DeterministicClient:
        def __init__(self, idx: int):
            self.idx = idx
            self.n = 0

        async def post(self, url, headers=None, json=None):
            runs_headers.append({"url": url, "idem": (headers or {}).get("Idempotency-Key", "")})
            if url.endswith("/assets"):
                self.n += 1
                # Different asset IDs across retries must NOT alter pose/splats Idempotency-Key!
                return _OkResp({"id": f"run{self.idx}_asset_{self.n}"})
            if url.endswith("/tasks:images2PosedRGBD"):
                frames = [
                    {
                        "imageAsset": {"assetId": f"run{self.idx}_asset_{i+1}"},
                        "camera": {"extrinsics": {"position": [0, 0, 0], "quaternion": [0, 0, 0, 1]}, "intrinsics": {"fx": 640, "fy": 640, "cx": 640, "cy": 360, "width": 1280, "height": 720}},
                        "depth": {"depthAsset": {"assetId": f"d_{i+1}"}},
                    }
                    for i in range(10)
                ]
                return _OkResp({"id": f"pose_op_{self.idx}", "done": True, "response": {"frames": frames}})
            if url.endswith("/tasks:posedImages2Splats"):
                return _OkResp(
                    {
                        "id": f"splat_op_{self.idx}",
                        "done": True,
                        "response": {"draftSplat": {"url": "https://storage.googleapis.com/b/draft.spz"}},
                    }
                )
            raise AssertionError(url)

    class _OkResp:
        status_code = 200
        headers = {}

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    for idx in (1, 2):
        asyncio.run(
            atlas_client.run_atlas_pipeline(
                _DeterministicClient(idx),
                api_key="atl-idem-key",
                panorama_bytes=pano,
                project_label="room-123",
                slice_count=8,
            )
        )

    pose_keys = [h["idem"] for h in runs_headers if h["url"].endswith("/tasks:images2PosedRGBD")]
    splat_keys = [h["idem"] for h in runs_headers if h["url"].endswith("/tasks:posedImages2Splats")]
    assert len(pose_keys) == 2 and pose_keys[0] == pose_keys[1] and pose_keys[0]
    assert len(splat_keys) == 2 and splat_keys[0] == splat_keys[1] and splat_keys[0]


def test_atlas_submit_attempts_capped_per_job(client, monkeypatch):
    monkeypatch.setenv("ATLAS_MAX_SUBMIT_ATTEMPTS_PER_JOB", "1")
    created = client.post("/api/v1/projects", json={"name": "Submit Cap"}).json()
    pid = created["id"]
    key = panorama_key(pid)
    get_storage().put_bytes(key, _pano_bytes(), "image/jpeg")
    db.update_project(pid, status="stitched", panorama_key=key, active_job=None)
    job = db.create_job(pid, "world")
    byok.set_project_world_key(pid, "atl-cap")

    submits = 0

    async def fake_gen(*args, **kwargs):
        nonlocal submits
        submits += 1
        await kwargs["on_submitting"]("draft")
        await kwargs["on_submitting"]("full")  # 2nd submit in same job exceeds cap=1!
        return {"provider": "atlas", "world_id": "op-never", "splat_url": "https://cdn.example/x.spz", "operation_id": "op-never"}

    monkeypatch.setattr(pipeline, "generate_world", fake_gen)
    asyncio.run(pipeline.run_job(job.id))
    assert submits == 1
    assert db.get_project(pid).status == "failed"


def test_per_device_project_and_storage_caps_and_enqueue_key_cleanup(client, monkeypatch):
    tok = client.raw.post("/api/v1/devices").json()["token"]
    auth = {"Authorization": f"Bearer {tok}"}

    monkeypatch.setenv("MAX_PROJECTS_PER_DEVICE", "2")
    r1 = client.raw.post("/api/v1/projects", json={"name": "P1"}, headers=auth)
    r2 = client.raw.post("/api/v1/projects", json={"name": "P2"}, headers=auth)
    r3 = client.raw.post("/api/v1/projects", json={"name": "P3"}, headers=auth)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r3.status_code == 429

    # Storage cap check on upload
    monkeypatch.setenv("MAX_STORAGE_BYTES_PER_DEVICE", "100")
    pid = r1.json()["id"]
    files = [("images", ("01.jpg", _jpeg_bytes(), "image/jpeg")), ("images", ("02.jpg", _jpeg_bytes(), "image/jpeg"))]
    up = client.raw.post(f"/api/v1/projects/{pid}/upload?auto_3d=false", files=files, headers=auth)
    assert up.status_code == 413

    # Non-queued enqueue clears BYOK key
    monkeypatch.setenv("MAX_STORAGE_BYTES_PER_DEVICE", "0")
    import routers.projects as rp

    async def fake_enqueue_busy(*args, **kwargs):
        return ("busy", None)

    monkeypatch.setattr(rp, "enqueue_job", fake_enqueue_busy)
    assert byok.get_project_world_key(pid) is None
    busy_3d = client.raw.post(f"/api/v1/projects/{pid}/upload?auto_3d=true", files=files, headers={**auth, "X-Atlas-Key": "atl-leaked"})
    assert busy_3d.status_code == 409
    assert byok.get_project_world_key(pid) is None

    # Ensure zero /marble/v1/worlds/ paths remain anywhere in backend/
    for pyf in (REPO_ROOT / "backend").rglob("*.py"):
        if "tests" in pyf.parts:
            continue
        assert "/marble/v1/worlds" not in pyf.read_text(), f"Found leftover Marble API path in {pyf}"


async def _async_none():
    return None
