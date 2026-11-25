from __future__ import annotations

import io
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from db import get_project
import services.byok as byok_store
from services.staging import StagingProviderError, stage_image
from services.world_gen import ProviderFailure, _require_credits, _resolve_world_key, clear_world_credits_cache


def _jpeg(seed: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (seed * 37 % 255, 100, 180)).save(buf, format="JPEG")
    return buf.getvalue()


def _create_buyer_key(client, monkeypatch, *, label: str = "buyer-1", monthly_world_limit: int = 0, expires_at: str | None = None):
    monkeypatch.setenv("API_KEY", "operator-admin-secret")
    payload: dict = {"label": label, "monthly_world_limit": monthly_world_limit}
    if expires_at is not None:
        payload["subscription_expires_at"] = expires_at
    resp = client.raw.post("/api/v1/admin/keys", headers={"X-Api-Key": "operator-admin-secret"}, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_open_device_registration_can_be_disabled(client, monkeypatch):
    monkeypatch.setenv("ALLOW_OPEN_DEVICE_REGISTRATION", "false")
    res = client.raw.post("/api/v1/devices")
    assert res.status_code == 403


def test_admin_key_lifecycle_auth_revoke_and_expiry(client, monkeypatch):
    monkeypatch.setenv("ALLOW_OPEN_DEVICE_REGISTRATION", "false")

    # Non-admin caller cannot mint or list buyer keys.
    no_admin = client.raw.post("/api/v1/admin/keys", json={"label": "unauth"})
    assert no_admin.status_code == 403

    issued = _create_buyer_key(client, monkeypatch, label="agensi-test-buyer", monthly_world_limit=5)
    raw_key = issued["api_key"]
    key_id = issued["id"]
    assert raw_key.startswith("f360_")

    # Buyer key authenticates via Bearer or X-Buyer-Key and scopes its own projects.
    created = client.raw.post(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"name": "Buyer Room"},
    )
    assert created.status_code == 200, created.text
    pid = created.json()["id"]

    listed = client.raw.get("/api/v1/projects", headers={"X-Buyer-Key": raw_key})
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()] == [pid]

    # Revoking the key immediately blocks future requests.
    rev = client.raw.post(f"/api/v1/admin/keys/{key_id}/revoke", headers={"X-Api-Key": "operator-admin-secret"})
    assert rev.status_code == 200
    assert rev.json()["revoked_at"] is not None

    blocked = client.raw.get("/api/v1/projects", headers={"Authorization": f"Bearer {raw_key}"})
    assert blocked.status_code == 401

    # Expired key is also rejected.
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    exp_issued = _create_buyer_key(client, monkeypatch, label="expired-buyer", expires_at=past)
    exp_res = client.raw.get("/api/v1/projects", headers={"X-Buyer-Key": exp_issued["api_key"]})
    assert exp_res.status_code == 401


def test_byok_required_refuses_missing_keys_and_never_falls_back(client, monkeypatch):
    monkeypatch.setenv("BYOK_REQUIRED", "true")
    monkeypatch.setenv("WORLDLABS_API_KEY", "server-wl-secret-never-use")
    monkeypatch.setenv("GEMINI_API_KEY", "server-gemini-secret-never-use")

    issued = _create_buyer_key(client, monkeypatch, label="byok-buyer")
    buyer_headers = {"Authorization": f"Bearer {issued['api_key']}"}
    pid = client.raw.post("/api/v1/projects", headers=buyer_headers, json={"name": "BYOK Room"}).json()["id"]
    files = [("images", (f"{i:02d}.jpg", _jpeg(i), "image/jpeg")) for i in range(2)]
    meta = json.dumps({"hfov_deg": 90, "yaws_deg": [0, 180]})

    # Upload with default auto_3d=true and no X-WorldLabs-Key must refuse.
    refused = client.raw.post(
        f"/api/v1/projects/{pid}/upload",
        headers=buyer_headers,
        files=files,
        data={"metadata": meta},
    )
    assert refused.status_code == 400
    assert "X-Atlas-Key" in refused.json()["detail"]

    # Upload with auto_3d=false stitches without needing a World Labs key.
    stitched = client.raw.post(
        f"/api/v1/projects/{pid}/upload?auto_3d=false",
        headers=buyer_headers,
        files=files,
        data={"metadata": meta},
    )
    assert stitched.status_code == 200, stitched.text
    proj = client.raw.get(f"/api/v1/projects/{pid}", headers=buyer_headers).json()
    assert proj["status"] == "stitched"
    assert proj["has_original"] is True

    # Stage without X-Gemini-Key must refuse.
    stage_refused = client.raw.post(
        f"/api/v1/projects/{pid}/stage",
        headers=buyer_headers,
        json={"prompt": "Modern sofa"},
    )
    assert stage_refused.status_code == 400
    assert "X-Gemini-Key" in stage_refused.json()["detail"]

    # Generate-3D without X-WorldLabs-Key must refuse.
    g3d_refused = client.raw.post(
        f"/api/v1/projects/{pid}/generate-3d",
        headers=buyer_headers,
    )
    assert g3d_refused.status_code == 400
    assert "X-Atlas-Key" in g3d_refused.json()["detail"]

    # Supplying buyer keys passes them directly and clears ephemeral memory after running.
    captured: dict[str, object] = {}

    async def fake_stage(image_bytes, prompt, before_generate=None, *, api_key=None, require_byok=False):
        captured["stage_key"] = api_key
        captured["stage_require_byok"] = require_byok
        if before_generate:
            before_generate()
        return image_bytes, "gemini", None

    async def fake_world(image, label, **kwargs):
        captured["world_key"] = kwargs.get("api_key")
        captured["world_require_byok"] = kwargs.get("require_byok")
        return {
            "provider": "worldlabs",
            "world_id": "world-byok-1",
            "marble_url": "https://marble.worldlabs.ai/world/world-byok-1",
            "splat_url": None,
            "mesh_url": None,
            "thumbnail_url": None,
            "operation_id": "op-byok-1",
        }

    monkeypatch.setattr("services.pipeline.stage_image", fake_stage)
    monkeypatch.setattr("services.pipeline.generate_world", fake_world)

    staged_ok = client.raw.post(
        f"/api/v1/projects/{pid}/stage",
        headers={**buyer_headers, "X-Gemini-Key": "buyer-gemini-123"},
        json={"prompt": "Warm Scandinavian living room"},
    )
    assert staged_ok.status_code == 200, staged_ok.text
    assert captured["stage_key"] == "buyer-gemini-123"
    assert captured["stage_require_byok"] is True
    assert byok_store._stage_keys_by_project.get(pid) is None

    world_ok = client.raw.post(
        f"/api/v1/projects/{pid}/generate-3d",
        headers={**buyer_headers, "X-WorldLabs-Key": "buyer-wl-456"},
    )
    assert world_ok.status_code == 200, world_ok.text
    assert captured["world_key"] == "buyer-wl-456"
    assert captured["world_require_byok"] is True
    assert byok_store.get_project_world_key(pid) is None


def test_service_layer_byok_never_falls_back_to_server_keys(monkeypatch):
    monkeypatch.setenv("WORLDLABS_API_KEY", "server-wl-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "server-gemini-secret")

    with pytest.raises(ProviderFailure):
        _resolve_world_key(None, require_byok=True)

    with pytest.raises(StagingProviderError):
        asyncio.run(stage_image(_jpeg(1), "Modern sofa", api_key=None, require_byok=True))


def test_monthly_world_cap_and_byok_exempt_from_global_server_caps(client, monkeypatch):
    monkeypatch.setenv("WORLDLABS_API_KEY", "server-wl-key")
    monkeypatch.setenv("WORLDLABS_JOBS_PER_DEVICE_PER_MONTH", "1")

    async def fake_world(image, label, **kwargs):
        return {
            "provider": "worldlabs",
            "world_id": "world-m1",
            "marble_url": "https://marble.worldlabs.ai/world/world-m1",
            "splat_url": None,
            "mesh_url": None,
            "thumbnail_url": None,
            "operation_id": "op-m1",
        }

    monkeypatch.setattr("services.pipeline.generate_world", fake_world)

    issued = _create_buyer_key(client, monkeypatch, label="capped-buyer", monthly_world_limit=1)
    headers = {"Authorization": f"Bearer {issued['api_key']}"}
    files = [("images", (f"{i:02d}.jpg", _jpeg(i), "image/jpeg")) for i in range(2)]
    meta = json.dumps({"hfov_deg": 90, "yaws_deg": [0, 180]})

    p1 = client.raw.post("/api/v1/projects", headers=headers, json={"name": "M1"}).json()["id"]
    r1 = client.raw.post(f"/api/v1/projects/{p1}/upload", headers=headers, files=files, data={"metadata": meta})
    assert r1.status_code == 200
    assert client.raw.get(f"/api/v1/projects/{p1}", headers=headers).json()["status"] == "complete"

    p2 = client.raw.post("/api/v1/projects", headers=headers, json={"name": "M2"}).json()["id"]
    r2 = client.raw.post(f"/api/v1/projects/{p2}/upload", headers=headers, files=files, data={"metadata": meta})
    assert r2.status_code == 200
    assert client.raw.get(f"/api/v1/projects/{p2}", headers=headers).json()["status"] == "stitched"


def test_worldlabs_hard_credit_floor_blocks_low_balance(monkeypatch):
    clear_world_credits_cache()
    monkeypatch.setenv("ATLAS_API_KEY", "server-atlas-floor-key")
    monkeypatch.setenv("WORLDLABS_HARD_CREDIT_FLOOR", "5000")

    async def fake_balance(api_key=None):
        return 5200.0

    monkeypatch.setattr("services.world_gen.world_credits_balance", fake_balance)
    # 5200 remaining - 520 needed = 4680 < 5000 floor -> must block server key
    with pytest.raises(ProviderFailure, match="credits"):
        asyncio.run(_require_credits("atlas-draft"))
    clear_world_credits_cache()


def test_idempotency_key_replays_and_rejects_mismatched_payload(client, monkeypatch):
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-idem")
    calls = {"n": 0}

    async def fake_world(image, label, **kwargs):
        calls["n"] += 1
        return {
            "provider": "worldlabs",
            "world_id": "world-idem",
            "marble_url": "https://marble.worldlabs.ai/world/world-idem",
            "splat_url": None,
            "mesh_url": None,
            "thumbnail_url": None,
            "operation_id": "op-idem",
        }

    monkeypatch.setattr("services.pipeline.generate_world", fake_world)

    issued = _create_buyer_key(client, monkeypatch, label="idem-buyer")
    headers = {"Authorization": f"Bearer {issued['api_key']}"}
    pid = client.raw.post("/api/v1/projects", headers=headers, json={"name": "Idem"}).json()["id"]
    files = [("images", (f"{i:02d}.jpg", _jpeg(i), "image/jpeg")) for i in range(2)]
    meta = json.dumps({"hfov_deg": 90, "yaws_deg": [0, 180]})

    first = client.raw.post(
        f"/api/v1/projects/{pid}/upload",
        headers={**headers, "Idempotency-Key": "upload-retry-1"},
        files=files,
        data={"metadata": meta},
    )
    assert first.status_code == 200
    assert calls["n"] == 1

    # Replaying the exact same Idempotency-Key and payload returns cached response without re-running.
    replay = client.raw.post(
        f"/api/v1/projects/{pid}/upload",
        headers={**headers, "Idempotency-Key": "upload-retry-1"},
        files=files,
        data={"metadata": meta},
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert calls["n"] == 1

    # Reusing the same Idempotency-Key with different payload returns 409 Conflict.
    diff_files = [("images", (f"{i:02d}.jpg", _jpeg(i + 10), "image/jpeg")) for i in range(2)]
    conflict = client.raw.post(
        f"/api/v1/projects/{pid}/upload",
        headers={**headers, "Idempotency-Key": "upload-retry-1"},
        files=diff_files,
        data={"metadata": meta},
    )
    assert conflict.status_code == 409


def test_share_revoke_invalidates_previous_share_and_preview_tokens(client, monkeypatch):
    issued = _create_buyer_key(client, monkeypatch, label="share-buyer")
    headers = {"Authorization": f"Bearer {issued['api_key']}"}
    pid = client.raw.post("/api/v1/projects", headers=headers, json={"name": "Share Room"}).json()["id"]
    files = [("images", (f"{i:02d}.jpg", _jpeg(i), "image/jpeg")) for i in range(2)]
    meta = json.dumps({"hfov_deg": 90, "yaws_deg": [0, 180]})
    up = client.raw.post(
        f"/api/v1/projects/{pid}/upload?auto_3d=false",
        headers=headers,
        files=files,
        data={"metadata": meta},
    )
    assert up.status_code == 200

    before = get_project(pid)
    assert before is not None
    old_share = before.share_token
    old_preview = before.preview_token
    assert client.raw.get(f"/v/{old_share}").status_code == 200
    assert client.raw.get(f"/p/{old_preview}").status_code == 200

    rev = client.raw.post(f"/api/v1/projects/{pid}/share/revoke", headers=headers)
    assert rev.status_code == 200
    assert client.raw.get(f"/v/{old_share}").status_code == 404
    assert client.raw.get(f"/p/{old_preview}").status_code == 404
    after = get_project(pid)
    assert after is not None
    assert after.share_token != old_share
    assert after.preview_token != old_preview
