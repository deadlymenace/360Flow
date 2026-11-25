"""World Labs Atlas Developer API v2 client and offline schema validator.

API base: https://api.atlas.worldlabs.ai/api/v2
Auth header: WLT-Api-Key
Pipeline for 360Flow Option A (panorama slices -> splats):
  1. Slice equirectangular panorama into 8-12 overlapping pinhole JPEG crops.
  2. Upload crops via POST /api/v2/assets (inline base64 < 10 MiB).
  3. Pose crops and estimate linear-depth EXRs via POST /api/v2/tasks:images2PosedRGBD.
  4. Optionally synthesize translated baseline views via POST /api/v2/tasks:atlasGenerate.
  5. Reconstruct Gaussian splats (.spz) via POST /api/v2/tasks:posedImages2Splats
     (draft mode refine=false by default: 500 credits / $0.50 + 2 credits/image).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import logging
import math
import random
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from services.pano_slicer import PanoSlice, SUPPORTED_ATLAS_GRIDS, slice_equirectangular

logger = logging.getLogger(__name__)

ATLAS_API_BASE_URL = "https://api.atlas.worldlabs.ai/api/v2"
MAX_INLINE_ASSET_BYTES = 10 * 1024 * 1024
MAX_IDEMPOTENCY_LEN = 128

# Public Atlas pricing rate card (1,000 credits = $1.00 USD)
ATLAS_CREDITS_PER_USD = 1000.0
ATLAS_POSE_CREDITS_PER_IMAGE = 2
ATLAS_GENERATE_FIRST_RGBD_CREDITS = 50
ATLAS_GENERATE_EXTRA_RGBD_CREDITS = 10
ATLAS_SPLAT_DRAFT_CREDITS = 500
ATLAS_SPLAT_REFINED_CREDITS = 5000


class AtlasError(Exception):
    def __init__(self, client_message: str, detail: str, status_code: int | None = None):
        super().__init__(detail)
        self.client_message = client_message
        self.detail = detail
        self.status_code = status_code


class AtlasRateLimit(AtlasError):
    pass


def valid_atlas_base_url(raw: str | None) -> str:
    candidate = (raw or ATLAS_API_BASE_URL).strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Atlas API base URL must be https:// without credentials")
    return candidate


def _idempotency_key(prefix: str, seed_parts: list[str]) -> str:
    h = hashlib.sha256("|".join(seed_parts).encode("utf-8")).hexdigest()[:40]
    key = f"{prefix}-{h}"
    return key[:MAX_IDEMPOTENCY_LEN]


def estimate_atlas_credits(
    *,
    slice_count: int,
    draft: bool = True,
    synthesize_baseline: bool = False,
    baseline_views: int = 4,
) -> dict[str, Any]:
    pose_credits = slice_count * ATLAS_POSE_CREDITS_PER_IMAGE
    gen_credits = 0
    if synthesize_baseline and baseline_views > 0:
        gen_credits = ATLAS_GENERATE_FIRST_RGBD_CREDITS + max(0, baseline_views - 1) * ATLAS_GENERATE_EXTRA_RGBD_CREDITS
    splat_credits = ATLAS_SPLAT_DRAFT_CREDITS if draft else ATLAS_SPLAT_REFINED_CREDITS
    total = pose_credits + gen_credits + splat_credits
    return {
        "pose_credits": pose_credits,
        "generate_credits": gen_credits,
        "splat_credits": splat_credits,
        "total_credits": total,
        "estimated_usd": round(total / ATLAS_CREDITS_PER_USD, 4),
        "mode": "draft" if draft else "refined",
    }


def build_asset_create_request(display_name: str, image_bytes: bytes, mime_type: str = "image/jpeg") -> dict[str, Any]:
    if len(image_bytes) >= MAX_INLINE_ASSET_BYTES:
        raise ValueError(f"Asset {display_name} exceeds Atlas 10 MiB inline base64 cap")
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "asset": {
            "displayName": display_name[:256],
            "base64": f"data:{mime_type};base64,{encoded}",
        }
    }


def build_images_to_posed_rgbd_request(
    asset_ids: list[str],
    *,
    target_resolution: tuple[int, int] = (1280, 720),
    canonicalize_to_first_frame: bool = False,
    refine_poses: bool = True,
) -> dict[str, Any]:
    if not (1 <= len(asset_ids) <= 64):
        raise ValueError("images2PosedRGBD requires between 1 and 64 frames")
    if target_resolution not in SUPPORTED_ATLAS_GRIDS:
        raise ValueError(f"Unsupported targetResolution {target_resolution}")
    return {
        "base64Outputs": False,
        "frames": [{"imageAsset": {"assetId": aid}} for aid in asset_ids],
        "canonicalizeToFirstFrame": bool(canonicalize_to_first_frame),
        "targetResolution": [int(target_resolution[0]), int(target_resolution[1])],
        "refinePoses": bool(refine_poses),
    }


def build_translated_target_cameras(
    anchor_slices: list[PanoSlice],
    *,
    baseline_m: float = 0.35,
    max_views: int = 4,
) -> list[dict[str, Any]]:
    """Build targetCameras with non-zero lateral translation for atlasGenerate(returnDepth=True).

    Atlas rejects returnDepth=True when all cameras share one center (pure rotation).
    Translating along the room circle perpendicular to view direction provides a synthetic baseline.
    """
    if baseline_m <= 0:
        raise ValueError("baseline_m must be > 0 to satisfy Atlas non-pure-rotation depth constraint")
    chosen = anchor_slices[:max(1, min(max_views, len(anchor_slices)))]
    targets: list[dict[str, Any]] = []
    for idx, sl in enumerate(chosen):
        ang = math.radians(sl.yaw_deg + (90.0 if idx % 2 == 0 else -90.0))
        dx = round(baseline_m * math.sin(ang), 4)
        dz = round(-baseline_m * math.cos(ang), 4)
        cam = json.loads(json.dumps(sl.camera))
        cam["extrinsics"]["position"] = [dx, 0.0, dz]
        targets.append(cam)
    return targets


def build_atlas_generate_request(
    context_frames: list[dict[str, Any]],
    target_cameras: list[dict[str, Any]],
    *,
    prompt: str | None = None,
    return_depth: bool = True,
) -> dict[str, Any]:
    if not context_frames:
        raise ValueError("atlasGenerate requires at least 1 contextFrame")
    if not target_cameras:
        raise ValueError("atlasGenerate requires at least 1 targetCamera")
    body: dict[str, Any] = {
        "base64Outputs": False,
        "contextFrames": context_frames,
        "targetCameras": target_cameras,
        "enhancePrompt": True,
        "returnDepth": bool(return_depth),
    }
    if prompt and prompt.strip():
        body["prompt"] = prompt.strip()
    return body


def build_posed_images_to_splats_request(
    posed_frames: list[dict[str, Any]],
    *,
    draft: bool = True,
    prompt: str | None = None,
) -> dict[str, Any]:
    max_frames = 32 if draft else 512
    if not (2 <= len(posed_frames) <= max_frames):
        raise ValueError(f"posedImages2Splats requires 2 to {max_frames} posedFrames (got {len(posed_frames)})")
    body: dict[str, Any] = {
        "enhancePrompt": True,
        "posedFrames": posed_frames,
    }
    if draft:
        # Documented in https://docs.atlas.worldlabs.ai/posed-images-to-splats.md (500 credits / draftSplat)
        body["refine"] = False
    if prompt and prompt.strip():
        body["prompt"] = prompt.strip()
    return body


def _validate_camera(cam: dict[str, Any], label: str, errors: list[str]) -> tuple[int, int] | None:
    if not isinstance(cam, dict):
        errors.append(f"{label}: camera must be an object")
        return None
    ext = cam.get("extrinsics")
    intr = cam.get("intrinsics")
    if not isinstance(ext, dict) or not isinstance(intr, dict):
        errors.append(f"{label}: camera requires extrinsics and intrinsics")
        return None
    pos = ext.get("position")
    quat = ext.get("quaternion")
    coord = ext.get("coordinateSystem", "rub")
    if not isinstance(pos, list) or len(pos) != 3 or not all(isinstance(v, (int, float)) for v in pos):
        errors.append(f"{label}: extrinsics.position must be 3 numbers")
    if not isinstance(quat, list) or len(quat) != 4 or not all(isinstance(v, (int, float)) for v in quat):
        errors.append(f"{label}: extrinsics.quaternion must be 4 XYZW numbers")
    else:
        norm = math.sqrt(sum(float(v) * float(v) for v in quat))
        if abs(norm - 1.0) > 1e-3:
            errors.append(f"{label}: extrinsics.quaternion is not unit length (norm={norm:.4f})")
    if coord not in {"rub", "rdf"}:
        errors.append(f"{label}: coordinateSystem must be 'rub' or 'rdf'")
    for req_k in ("fx", "fy", "cx", "cy", "width", "height"):
        if req_k not in intr:
            errors.append(f"{label}: intrinsics missing {req_k}")
    w, h = intr.get("width"), intr.get("height")
    if (w, h) not in SUPPORTED_ATLAS_GRIDS:
        errors.append(f"{label}: grid {(w, h)} is not in supported Atlas grids {SUPPORTED_ATLAS_GRIDS}")
        return None
    if not (isinstance(intr.get("fx"), (int, float)) and intr["fx"] > 0):
        errors.append(f"{label}: fx must be > 0")
    if not (isinstance(intr.get("fy"), (int, float)) and intr["fy"] > 0):
        errors.append(f"{label}: fy must be > 0")
    return (int(w), int(h))


def validate_atlas_payloads(
    *,
    slices: list[PanoSlice],
    asset_requests: list[dict[str, Any]],
    pose_request: dict[str, Any],
    splats_request: dict[str, Any],
    generate_request: dict[str, Any] | None = None,
    idempotency_keys: list[str] | None = None,
) -> dict[str, Any]:
    """Validate all Atlas v2 payloads strictly against docs.atlas.worldlabs.ai OpenAPI rules."""
    errors: list[str] = []
    warnings: list[str] = []

    if not (8 <= len(slices) <= 12):
        errors.append(f"Expected 8-12 slices, got {len(slices)}")

    for i, req in enumerate(asset_requests):
        asset = req.get("asset", {})
        dn = asset.get("displayName", "")
        b64 = asset.get("base64", "")
        if not dn or len(dn) > 256:
            errors.append(f"asset[{i}]: invalid displayName length")
        if not isinstance(b64, str) or not b64.startswith("data:image/"):
            errors.append(f"asset[{i}]: missing data URL base64 payload")
        elif len(b64.encode("ascii")) > int(MAX_INLINE_ASSET_BYTES * 1.4):
            errors.append(f"asset[{i}]: inline base64 too large")

    frames = pose_request.get("frames", [])
    if not (1 <= len(frames) <= 64):
        errors.append(f"images2PosedRGBD: frames count {len(frames)} outside [1, 64]")
    for i, fr in enumerate(frames):
        if any(k in fr for k in ("camera", "depth", "maskAsset")):
            errors.append(f"images2PosedRGBD.frames[{i}]: must not carry camera, depth, or maskAsset")
        if not isinstance(fr.get("imageAsset"), dict):
            errors.append(f"images2PosedRGBD.frames[{i}]: missing imageAsset")
    tr = pose_request.get("targetResolution")
    if not isinstance(tr, list) or len(tr) != 2 or (tr[0], tr[1]) not in SUPPORTED_ATLAS_GRIDS:
        errors.append(f"images2PosedRGBD: invalid targetResolution {tr}")

    if generate_request is not None:
        ctx = generate_request.get("contextFrames", [])
        tgts = generate_request.get("targetCameras", [])
        if not ctx or not tgts:
            errors.append("atlasGenerate: contextFrames and targetCameras must be non-empty")
        positions: list[tuple[float, float, float]] = []
        for i, cf in enumerate(ctx):
            _validate_camera(cf.get("camera", {}), f"atlasGenerate.contextFrames[{i}]", errors)
            pos = cf.get("camera", {}).get("extrinsics", {}).get("position")
            if isinstance(pos, list) and len(pos) == 3:
                positions.append((float(pos[0]), float(pos[1]), float(pos[2])))
            if not isinstance(cf.get("depth"), dict):
                errors.append(f"atlasGenerate.contextFrames[{i}]: context depth is required")
        for i, tc in enumerate(tgts):
            _validate_camera(tc, f"atlasGenerate.targetCameras[{i}]", errors)
            pos = tc.get("extrinsics", {}).get("position")
            if isinstance(pos, list) and len(pos) == 3:
                positions.append((float(pos[0]), float(pos[1]), float(pos[2])))
        if generate_request.get("returnDepth"):
            distinct = {(round(p[0], 4), round(p[1], 4), round(p[2], 4)) for p in positions}
            if len(distinct) < 2:
                errors.append("atlasGenerate(returnDepth=True): cameras cannot all share one center (needs baseline)")

    splat_frames = splats_request.get("posedFrames", [])
    is_draft = splats_request.get("refine") is False
    max_splat = 32 if is_draft else 512
    if not (2 <= len(splat_frames) <= max_splat):
        errors.append(f"posedImages2Splats: posedFrames count {len(splat_frames)} outside [2, {max_splat}]")
    grids: set[tuple[int, int]] = set()
    for i, pf in enumerate(splat_frames):
        if not isinstance(pf.get("imageAsset"), dict):
            errors.append(f"posedImages2Splats.posedFrames[{i}]: missing imageAsset")
        depth = pf.get("depth")
        if not isinstance(depth, dict) or ("depthAsset" not in depth and "logDepthAsset" not in depth):
            errors.append(f"posedImages2Splats.posedFrames[{i}]: depth.depthAsset or depth.logDepthAsset required")
        g = _validate_camera(pf.get("camera", {}), f"posedImages2Splats.posedFrames[{i}]", errors)
        if g is not None:
            grids.add(g)
    if len(grids) > 1:
        errors.append(f"posedImages2Splats: all posedFrames must share one grid, got {grids}")

    if is_draft:
        warnings.append(
            "OpenAPI spec gap: posed-images-to-splats.md prose documents refine=false & draftSplat ($0.50), "
            "while api-reference OpenAPI JSON omits refine/draftSplat under additionalProperties=false."
        )

    for key in idempotency_keys or []:
        if not key or len(key) > MAX_IDEMPOTENCY_LEN:
            errors.append(f"Invalid Idempotency-Key length ({len(key)})")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _make_synthetic_equirect(width: int = 512, height: int = 256) -> bytes:
    """Create a deterministic synthetic 2:1 equirectangular room panorama for offline dry-run."""
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    # Ceiling, walls with vertical stripes, and floor
    arr[: height // 3, :] = (230, 226, 218)
    arr[height // 3 : (2 * height) // 3, :] = (185, 198, 210)
    arr[(2 * height) // 3 :, :] = (135, 112, 92)
    for x in range(0, width, max(8, width // 16)):
        arr[height // 3 : (2 * height) // 3, x : x + 3] = (90, 115, 140)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# Need numpy in _make_synthetic_equirect
import numpy as np  # noqa: E402


def dry_run_atlas_pipeline(
    panorama_bytes: bytes | None = None,
    *,
    slice_count: int = 10,
    hfov_deg: float = 90.0,
    draft: bool = True,
    synthesize_baseline: bool = False,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Run slicing, Atlas payload assembly, and schema validation 100% offline."""
    raw = panorama_bytes if panorama_bytes is not None else _make_synthetic_equirect()
    slices = slice_equirectangular(raw, count=slice_count, hfov_deg=hfov_deg)

    asset_requests = [build_asset_create_request(sl.name, sl.image_bytes, sl.mime_type) for sl in slices]
    fake_asset_ids = [f"asset_dryrun_{sl.index:02d}" for sl in slices]
    pose_request = build_images_to_posed_rgbd_request(fake_asset_ids, target_resolution=(1280, 720), refine_poses=True)

    # Simulate the posed RGBD outputs returned by images2PosedRGBD (for offline validation)
    simulated_posed = [
        {
            "imageAsset": {"assetId": fake_asset_ids[sl.index]},
            "camera": sl.camera,
            "depth": {"depthAsset": {"assetId": f"asset_depth_{sl.index:02d}"}},
        }
        for sl in slices
    ]

    generate_request: dict[str, Any] | None = None
    baseline_views = 0
    if synthesize_baseline:
        target_cams = build_translated_target_cameras(slices, baseline_m=0.35, max_views=4)
        baseline_views = len(target_cams)
        generate_request = build_atlas_generate_request(
            simulated_posed[:4],
            target_cams,
            prompt=prompt,
            return_depth=True,
        )
        for j, cam in enumerate(target_cams):
            simulated_posed.append(
                {
                    "imageAsset": {"assetId": f"asset_gen_{j:02d}"},
                    "camera": cam,
                    "depth": {"depthAsset": {"assetId": f"asset_gen_depth_{j:02d}"}},
                }
            )

    splats_request = build_posed_images_to_splats_request(simulated_posed, draft=draft, prompt=prompt)
    idem_keys = [
        _idempotency_key("atlas-pose", [str(len(slices))]),
        _idempotency_key("atlas-splats", [str(len(simulated_posed)), str(draft)]),
    ]
    if generate_request is not None:
        idem_keys.append(_idempotency_key("atlas-gen", [str(baseline_views)]))

    validation = validate_atlas_payloads(
        slices=slices,
        asset_requests=asset_requests,
        pose_request=pose_request,
        splats_request=splats_request,
        generate_request=generate_request,
        idempotency_keys=idem_keys,
    )
    cost = estimate_atlas_credits(
        slice_count=len(slices),
        draft=draft,
        synthesize_baseline=synthesize_baseline,
        baseline_views=baseline_views,
    )
    return {
        "dry_run": True,
        "network_calls": 0,
        "api_base": ATLAS_API_BASE_URL,
        "slice_count": len(slices),
        "slice_grid": [1280, 720],
        "hfov_deg": hfov_deg,
        "pitch_rows_deg": sorted({sl.pitch_deg for sl in slices}),
        "sample_slice_camera": slices[0].camera,
        "endpoints": [
            f"POST {ATLAS_API_BASE_URL}/assets (x{len(asset_requests)})",
            f"POST {ATLAS_API_BASE_URL}/tasks:images2PosedRGBD",
            *([f"POST {ATLAS_API_BASE_URL}/tasks:atlasGenerate"] if generate_request is not None else []),
            f"POST {ATLAS_API_BASE_URL}/tasks:posedImages2Splats",
        ],
        "validation": validation,
        "cost_estimate": cost,
    }


def _retry_after_s(response: httpx.Response) -> float | None:
    raw = (response.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _raise_http_error(response: httpx.Response, context: str) -> None:
    status = response.status_code
    try:
        body = response.json()
    except Exception:
        body = {}
    err = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else body
    code = str(err.get("code") or "") if isinstance(err, dict) else ""
    msg = str(err.get("message") or body.get("detail") or f"HTTP {status}") if isinstance(err, dict) else f"HTTP {status}"
    if status == 402 or code in {"CREDITS_UNAVAILABLE", "SPEND_LIMIT_EXCEEDED"}:
        raise AtlasError("Atlas account credits or spend limit exhausted.", f"{context}: HTTP {status} ({code or msg})", 402)
    if status == 401 or status == 403:
        raise AtlasError("Atlas API key was rejected.", f"{context}: HTTP {status}", status)
    if status == 429 or code in {"RATE_LIMIT_EXCEEDED", "CONCURRENCY_LIMIT_EXCEEDED", "TASK_UNAVAILABLE"}:
        raise AtlasRateLimit("Atlas capacity limit reached. Try again shortly.", f"{context}: HTTP {status} ({code})", 429)
    raise AtlasError("3D generation failed on Atlas.", f"{context}: HTTP {status} ({msg[:160]})", status)


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    context: str,
    retry_attempts: int = 4,
    retry_base_s: float = 1.0,
    retry_max_s: float = 30.0,
) -> dict[str, Any]:
    for attempt in range(1, retry_attempts + 1):
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code < 400:
            data = resp.json()
            if not isinstance(data, dict):
                raise AtlasError("3D generation returned an invalid response.", f"{context}: non-object JSON", resp.status_code)
            return data
        if resp.status_code == 429 and attempt < retry_attempts:
            wait = _retry_after_s(resp)
            if wait is None:
                wait = min(retry_max_s, retry_base_s * (2 ** (attempt - 1)) + random.uniform(0.0, 0.5))
            await asyncio.sleep(wait)
            continue
        _raise_http_error(resp, context)
    raise AtlasRateLimit("Atlas capacity limit reached.", f"{context}: retries exhausted", 429)


async def poll_atlas_operation(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    operation_id: str,
    timeout_s: int,
    *,
    on_tick=None,
) -> dict[str, Any]:
    headers = {"WLT-Api-Key": api_key}
    deadline = asyncio.get_running_loop().time() + timeout_s
    delay = 2.0
    while True:
        if on_tick is not None:
            on_tick()
        resp = await client.get(f"{base_url}/operations/{operation_id}", headers=headers)
        if resp.status_code == 404:
            pass
        elif resp.status_code == 429:
            wait = _retry_after_s(resp) or min(15.0, delay)
            if asyncio.get_running_loop().time() + wait >= deadline:
                _raise_http_error(resp, f"poll {operation_id}")
            await asyncio.sleep(wait)
            continue
        elif resp.status_code >= 400:
            _raise_http_error(resp, f"poll {operation_id}")
        else:
            op = resp.json()
            if op.get("done") is True:
                if op.get("error"):
                    err = op["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    raise AtlasError("3D generation failed on Atlas.", f"operation {operation_id}: {msg}", 502)
                return op
        if asyncio.get_running_loop().time() >= deadline:
            raise AtlasError("3D generation timed out waiting for Atlas.", f"operation {operation_id} timed out", 504)
        await asyncio.sleep(delay)
        delay = min(10.0, delay * 1.4)


def extract_splat_asset(response_obj: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Extract (asset_id, url) from either draftSplat or refinedSplats."""
    if not isinstance(response_obj, dict):
        return None, None
    draft = response_obj.get("draftSplat")
    if isinstance(draft, dict) and (draft.get("assetId") or draft.get("url")):
        return draft.get("assetId"), draft.get("url")
    refined = response_obj.get("refinedSplats")
    if isinstance(refined, dict) and refined:
        # Pick smallest numerical splat tier if multiple exist, for fast web loading
        def _rank(item: tuple[str, Any]) -> int:
            digits = "".join(ch for ch in str(item[0]) if ch.isdigit())
            return int(digits) if digits else 10**9

        for _k, val in sorted(refined.items(), key=_rank):
            if isinstance(val, dict) and (val.get("assetId") or val.get("url")):
                return val.get("assetId"), val.get("url")
    return None, None


async def resolve_read_url(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    asset_id: str | None,
    existing_url: str | None,
) -> str | None:
    if existing_url and existing_url.startswith("https://"):
        return existing_url
    if not asset_id:
        return None
    resp = await client.post(
        f"{base_url}/assets/{asset_id}:createReadUrl",
        headers={"WLT-Api-Key": api_key},
        json={},
    )
    if resp.status_code >= 400:
        return None
    body = resp.json()
    url = body.get("url") if isinstance(body, dict) else None
    return url if isinstance(url, str) and url.startswith("https://") else None


async def run_atlas_pipeline(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    panorama_bytes: bytes,
    project_label: str,
    base_url: str = ATLAS_API_BASE_URL,
    slice_count: int = 10,
    hfov_deg: float = 90.0,
    draft: bool = True,
    synthesize_baseline: bool = False,
    pose_timeout_s: int = 300,
    splat_timeout_s: int = 1800,
    on_operation=None,
    on_submitting=None,
    on_tick=None,
) -> dict[str, Any]:
    """Execute the full Atlas v2 pipeline from equirectangular panorama bytes."""
    cleaned_key = (api_key or "").strip()
    if not cleaned_key:
        raise AtlasError("Atlas API key is required for 3D generation.", "Atlas API key missing", 400)
    root = valid_atlas_base_url(base_url)
    slices = slice_equirectangular(panorama_bytes, count=slice_count, hfov_deg=hfov_deg)

    # 1. Upload sliced pinhole JPEG crops as project assets
    asset_ids: list[str] = []
    for sl in slices:
        if on_tick is not None:
            on_tick()
        asset_req = build_asset_create_request(f"{project_label}-{sl.name}", sl.image_bytes, sl.mime_type)
        asset_resp = await _post_with_retry(
            client,
            f"{root}/assets",
            {"WLT-Api-Key": cleaned_key, "Content-Type": "application/json"},
            asset_req,
            context=f"upload {sl.name}",
        )
        aid = asset_resp.get("id")
        if not isinstance(aid, str) or not aid:
            raise AtlasError("Atlas asset upload failed.", "missing asset id", 502)
        asset_ids.append(aid)

    # 2. Estimate posed RGBD frames (cameras + linear-depth EXRs)
    pose_req = build_images_to_posed_rgbd_request(asset_ids, target_resolution=(1280, 720), refine_poses=True)
    pose_headers = {
        "WLT-Api-Key": cleaned_key,
        "Content-Type": "application/json",
        "Idempotency-Key": _idempotency_key("pose", [project_label, *asset_ids]),
    }
    pose_submit = await _post_with_retry(
        client, f"{root}/tasks:images2PosedRGBD", pose_headers, pose_req, context="images2PosedRGBD"
    )
    pose_op_id = pose_submit.get("id")
    if not isinstance(pose_op_id, str) or not pose_op_id:
        raise AtlasError("Atlas posing did not return an operation ID.", "missing pose operation id", 502)
    if pose_submit.get("done") is True and not pose_submit.get("error"):
        pose_op = pose_submit
    else:
        pose_op = await poll_atlas_operation(client, root, cleaned_key, pose_op_id, pose_timeout_s, on_tick=on_tick)

    posed_frames = []
    for fr in (pose_op.get("response") or {}).get("frames") or []:
        if isinstance(fr, dict) and fr.get("imageAsset") and fr.get("camera") and fr.get("depth"):
            posed_frames.append(
                {
                    "imageAsset": {"assetId": fr["imageAsset"]["assetId"]}
                    if fr["imageAsset"].get("assetId")
                    else fr["imageAsset"],
                    "camera": fr["camera"],
                    "depth": fr["depth"],
                }
            )

    # 3. Optionally synthesize translated baseline frames with atlasGenerate
    if synthesize_baseline and posed_frames:
        target_cams = build_translated_target_cameras(slices, baseline_m=0.35, max_views=4)
        gen_req = build_atlas_generate_request(posed_frames[:4], target_cams, return_depth=True)
        gen_headers = {
            "WLT-Api-Key": cleaned_key,
            "Content-Type": "application/json",
            "Idempotency-Key": _idempotency_key("gen", [project_label, pose_op_id]),
        }
        gen_submit = await _post_with_retry(
            client, f"{root}/tasks:atlasGenerate", gen_headers, gen_req, context="atlasGenerate"
        )
        gen_op_id = gen_submit.get("id")
        if isinstance(gen_op_id, str) and gen_op_id:
            gen_op = (
                gen_submit
                if gen_submit.get("done") is True and not gen_submit.get("error")
                else await poll_atlas_operation(client, root, cleaned_key, gen_op_id, pose_timeout_s, on_tick=on_tick)
            )
            for gf in (gen_op.get("response") or {}).get("frames") or []:
                if isinstance(gf, dict) and gf.get("imageAsset") and gf.get("camera") and gf.get("depth"):
                    posed_frames.append(
                        {
                            "imageAsset": {"assetId": gf["imageAsset"]["assetId"]}
                            if gf["imageAsset"].get("assetId")
                            else gf["imageAsset"],
                            "camera": gf["camera"],
                            "depth": gf["depth"],
                        }
                    )

    # 4. Reconstruct Gaussian splats (draft by default)
    phase = "draft" if draft else "full"
    if on_submitting is not None:
        await on_submitting(phase)
    splats_req = build_posed_images_to_splats_request(posed_frames, draft=draft)
    splats_headers = {
        "WLT-Api-Key": cleaned_key,
        "Content-Type": "application/json",
        "Idempotency-Key": _idempotency_key("splats", [project_label, pose_op_id, phase]),
    }
    splat_submit = await _post_with_retry(
        client, f"{root}/tasks:posedImages2Splats", splats_headers, splats_req, context="posedImages2Splats"
    )
    splat_op_id = splat_submit.get("id")
    if not isinstance(splat_op_id, str) or not splat_op_id:
        raise AtlasError("Atlas splat generation did not return an operation ID.", "missing splat op id", 502)
    if on_operation is not None:
        await on_operation(splat_op_id, phase)
    if splat_submit.get("done") is True and not splat_submit.get("error"):
        splat_op = splat_submit
    else:
        splat_op = await poll_atlas_operation(client, root, cleaned_key, splat_op_id, splat_timeout_s, on_tick=on_tick)

    asset_id, spz_url = extract_splat_asset(splat_op.get("response"))
    resolved_url = await resolve_read_url(client, root, cleaned_key, asset_id, spz_url)
    if not resolved_url:
        raise AtlasError("Atlas completed without a downloadable .spz splat URL.", "missing splat url", 502)

    return {
        "provider": "atlas",
        "world_id": splat_op_id,
        "marble_url": None,
        "splat_url": resolved_url,
        "splat_asset_id": asset_id,
        "mesh_url": None,
        "thumbnail_url": None,
        "operation_id": splat_op_id,
        "model": "atlas-draft" if draft else "atlas-refined",
        "slice_count": len(slices),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atlas v2 offline dry-run & schema validator")
    parser.add_argument("--dry-run", action="store_true", help="Validate slicing and Atlas payloads offline")
    parser.add_argument("--panorama", help="Optional equirectangular JPEG/PNG path (synthetic used if omitted)")
    parser.add_argument("--slices", type=int, default=10, choices=(8, 10, 12))
    parser.add_argument("--hfov", type=float, default=90.0)
    parser.add_argument("--refined", action="store_true", help="Validate refined mode ($5) instead of draft ($0.52)")
    parser.add_argument("--synthesize-baseline", action="store_true", help="Include atlasGenerate baseline synthesis")
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("Only --dry-run is permitted from CLI without a live API workflow.")
    pano_bytes = Path(args.panorama).read_bytes() if args.panorama else None
    report = dry_run_atlas_pipeline(
        pano_bytes,
        slice_count=args.slices,
        hfov_deg=args.hfov,
        draft=not args.refined,
        synthesize_baseline=args.synthesize_baseline,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["validation"]["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
