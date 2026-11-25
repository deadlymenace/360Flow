"""Offline tests for equirectangular->pinhole slicing and World Labs Atlas v2 client."""

from __future__ import annotations

import asyncio
import io
import json
import math
from urllib.parse import urlparse

import httpx
import numpy as np
import pytest
from PIL import Image

from services import atlas_client, pano_slicer


def _make_equirect_jpeg(width: int = 1024, height: int = 512) -> bytes:
    """Create a synthetic 2:1 equirectangular panorama with distinct color bands per azimuth."""
    x = np.linspace(0.0, 1.0, width, endpoint=False)[None, :]
    y = np.linspace(0.0, 1.0, height, endpoint=False)[:, None]
    r = np.clip(255.0 * (0.5 + 0.5 * np.sin(2.0 * np.pi * x)), 0, 255).astype(np.uint8)
    g = np.clip(255.0 * y, 0, 255).astype(np.uint8)
    b = np.clip(255.0 * (0.5 + 0.5 * np.cos(2.0 * np.pi * x)), 0, 255).astype(np.uint8)
    arr = np.stack(
        [np.broadcast_to(r, (height, width)), np.broadcast_to(g, (height, width)), np.broadcast_to(b, (height, width))],
        axis=-1,
    )
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.mark.parametrize("count", [8, 10, 12])
def test_slice_equirectangular_counts_and_intrinsics(count: int):
    pano = _make_equirect_jpeg()
    slices = pano_slicer.slice_equirectangular(pano, count=count, width=1280, height=720, hfov_deg=90.0)
    assert len(slices) == count
    pitches = {sl.pitch_deg for sl in slices}
    assert len(pitches) >= 2
    for sl in slices:
        assert sl.width == 1280
        assert sl.height == 720
        assert sl.mime_type == "image/jpeg"
        assert len(sl.image_bytes) > 1000
        ext = sl.camera["extrinsics"]
        intr = sl.camera["intrinsics"]
        assert ext["coordinateSystem"] == "rub"
        assert ext["position"] == [0.0, 0.0, 0.0]
        assert intr["width"] == 1280
        assert intr["height"] == 720
        assert math.isclose(intr["fx"], 640.0, rel_tol=1e-6)
        assert math.isclose(intr["fy"], 640.0, rel_tol=1e-6)
        assert math.isclose(intr["cx"], 640.0, rel_tol=1e-6)
        assert math.isclose(intr["cy"], 360.0, rel_tol=1e-6)
        q = ext["quaternion"]
        norm = math.sqrt(sum(v * v for v in q))
        assert math.isclose(norm, 1.0, rel_tol=1e-6)
    hashes = {sl.image_bytes[:200] + sl.image_bytes[-200:] for sl in slices}
    assert len(hashes) == count


def test_slice_equirectangular_rejects_too_small_or_bad_count():
    buf = io.BytesIO()
    Image.new("RGB", (32, 16), (10, 20, 30)).save(buf, format="JPEG")
    with pytest.raises(ValueError, match="too small"):
        pano_slicer.slice_equirectangular(buf.getvalue(), count=10)
    with pytest.raises(ValueError, match="8, 10, or 12"):
        pano_slicer.slice_equirectangular(_make_equirect_jpeg(), count=6)


def test_dry_run_atlas_pipeline_offline_and_validation():
    pano = _make_equirect_jpeg()
    report = atlas_client.dry_run_atlas_pipeline(pano, slice_count=10, draft=True, synthesize_baseline=False)
    assert report["network_calls"] == 0
    assert report["validation"]["valid"] is True
    assert report["validation"]["errors"] == []
    assert report["slice_count"] == 10
    assert report["cost_estimate"]["total_credits"] == 520
    assert report["cost_estimate"]["estimated_usd"] == 0.52

    report_gen = atlas_client.dry_run_atlas_pipeline(pano, slice_count=10, draft=True, synthesize_baseline=True)
    assert report_gen["validation"]["valid"] is True
    assert report_gen["cost_estimate"]["total_credits"] == 600
    assert report_gen["cost_estimate"]["estimated_usd"] == 0.6


def test_validate_atlas_payloads_catches_pure_rotation_depth_generation():
    pano = _make_equirect_jpeg()
    slices = pano_slicer.slice_equirectangular(pano, count=8)
    asset_reqs = [atlas_client.build_asset_create_request(sl.name, sl.image_bytes) for sl in slices]
    asset_ids = [f"asset-{i}" for i in range(8)]
    pose_req = atlas_client.build_images_to_posed_rgbd_request(asset_ids)
    posed_frames = [
        {
            "imageAsset": {"assetId": aid},
            "camera": sl.camera,
            "depth": {"depthAsset": {"assetId": f"depth-{i}"}},
        }
        for i, (aid, sl) in enumerate(zip(asset_ids, slices))
    ]
    bad_gen_req = atlas_client.build_atlas_generate_request(
        posed_frames[:2],
        [sl.camera for sl in slices[:2]],  # all [0,0,0] positions!
        return_depth=True,
    )
    splat_req = atlas_client.build_posed_images_to_splats_request(posed_frames, draft=True)
    res = atlas_client.validate_atlas_payloads(
        slices=slices,
        asset_requests=asset_reqs,
        pose_request=pose_req,
        splats_request=splat_req,
        generate_request=bad_gen_req,
    )
    assert res["valid"] is False
    assert any("share one center" in err for err in res["errors"])


def test_run_atlas_pipeline_mock_transport_with_optional_baseline(monkeypatch):
    async def _fast_sleep(_s):
        return None
    monkeypatch.setattr(atlas_client.asyncio, "sleep", _fast_sleep)
    pano = _make_equirect_jpeg()
    calls: list[tuple[str, str]] = []
    asset_counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("WLT-Api-Key") == "atlas-byok-secret"
        assert urlparse(str(request.url)).netloc == "api.atlas.worldlabs.ai"
        path = request.url.path
        calls.append((request.method, path))
        if request.method == "POST" and path == "/api/v2/assets":
            asset_counter["n"] += 1
            return httpx.Response(200, json={"id": f"asset-{asset_counter['n']}"})
        if request.method == "POST" and path == "/api/v2/tasks:images2PosedRGBD":
            body = json.loads(request.content)
            assert len(body["frames"]) == 8
            return httpx.Response(200, json={"id": "op-pose-1", "done": False})
        if request.method == "GET" and path == "/api/v2/operations/op-pose-1":
            frames = [
                {
                    "imageAsset": {"assetId": f"asset-{i+1}"},
                    "camera": pano_slicer.pinhole_camera_dict(i * 45.0, 0.0)[1],
                    "depth": {"depthAsset": {"assetId": f"depth-{i+1}"}},
                }
                for i in range(8)
            ]
            return httpx.Response(200, json={"id": "op-pose-1", "done": True, "response": {"frames": frames}})
        if request.method == "POST" and path == "/api/v2/tasks:atlasGenerate":
            body = json.loads(request.content)
            assert body["returnDepth"] is True
            assert len({tuple(c["extrinsics"]["position"]) for c in body["targetCameras"]}) > 1
            return httpx.Response(200, json={"id": "op-gen-1", "done": False})
        if request.method == "GET" and path == "/api/v2/operations/op-gen-1":
            return httpx.Response(
                200,
                json={
                    "id": "op-gen-1",
                    "done": True,
                    "response": {
                        "frames": [
                            {
                                "imageAsset": {"assetId": "gen-img-1"},
                                "camera": pano_slicer.pinhole_camera_dict(0.0, 0.0, position=(0.35, 0.0, 0.0))[1],
                                "depth": {"depthAsset": {"assetId": "gen-depth-1"}},
                            }
                        ]
                    },
                },
            )
        if request.method == "POST" and path == "/api/v2/tasks:posedImages2Splats":
            body = json.loads(request.content)
            assert body["refine"] is False
            assert len(body["posedFrames"]) == 9
            return httpx.Response(200, json={"id": "op-splat-1", "done": False})
        if request.method == "GET" and path == "/api/v2/operations/op-splat-1":
            return httpx.Response(
                200,
                json={
                    "id": "op-splat-1",
                    "done": True,
                    "response": {
                        "draftSplat": {
                            "assetId": "splat-asset-99",
                            "url": "https://storage.googleapis.com/atlas-bucket/draft.spz",
                        }
                    },
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await atlas_client.run_atlas_pipeline(
                client,
                api_key="atlas-byok-secret",
                panorama_bytes=pano,
                project_label="test-proj",
                slice_count=8,
                draft=True,
                synthesize_baseline=True,
            )

    result = asyncio.run(run())
    assert result["splat_asset_id"] == "splat-asset-99"
    assert result["splat_url"] == "https://storage.googleapis.com/atlas-bucket/draft.spz"
    assert result["operation_id"] == "op-splat-1"
    assert ("POST", "/api/v2/tasks:atlasGenerate") in calls


def test_run_atlas_pipeline_maps_402_spend_limit():
    pano = _make_equirect_jpeg()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"message": "Project spend_limit exceeded"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await atlas_client.run_atlas_pipeline(
                client,
                api_key="atlas-byok-secret",
                panorama_bytes=pano,
                project_label="test-402",
                slice_count=8,
            )

    with pytest.raises(atlas_client.AtlasError) as exc_info:
        asyncio.run(run())
    assert exc_info.value.status_code == 402


def test_atlas_world_ready_and_spark_viewer_without_marble_url(client):
    from db import update_project
    from services.world_assets import world_asset_key
    from storage import get_storage

    created = client.post("/api/v1/projects", json={"name": "Atlas Spark Room"}).json()
    project_id = created["id"]
    token = created["share_token"]
    get_storage().put_bytes(f"panoramas/{project_id}.jpg", _make_equirect_jpeg(256, 128), "image/jpeg")
    get_storage().put_bytes(world_asset_key(project_id, "original", "spz"), b"SPZ_FAKE_DATA", "application/octet-stream")
    update_project(
        project_id,
        status="complete",
        panorama_key=f"panoramas/{project_id}.jpg",
        world_provider="atlas",
        world_id="splat-asset-99",
        world_marble_url=None,
        world_splat_url="https://storage.googleapis.com/atlas-bucket/draft.spz",
    )

    proj = client.get(f"/api/v1/projects/{project_id}").json()
    assert proj["world_ready"] is True
    assert proj["world_draft_ready"] is False
    assert proj["world_provider"] == "atlas"

    ws = client.get(f"/api/v1/projects/{project_id}/generate-3d/status").json()
    assert ws["assets"]["ready"] is True

    page = client.get(f"/v/{token}/world")
    assert page.status_code == 200
    assert "world.spz" in page.text
    assert "marble.worldlabs.ai" not in page.text

    spz = client.get(f"/v/{token}/world.spz")
    assert spz.status_code == 200
    assert spz.content == b"SPZ_FAKE_DATA"
