"""World Labs Marble generation. Uses the mock when WORLDLABS_API_KEY is unset."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from config import get_settings
from services.world_gen_mock import mock_generate_3d

logger = logging.getLogger(__name__)

WORLDLABS_BASE = "https://api.worldlabs.ai"


def build_world_request(display_name: str, media_asset_id: str, model: str, public: bool) -> dict:
    """Image-to-world body. ``is_pano`` is true because the stitcher emits a 2:1 equirect."""
    name = " ".join(display_name.split())[:64] or "360Flow room"
    return {
        "display_name": name,
        "model": model,
        "permission": {"public": public},
        "world_prompt": {
            "type": "image",
            "is_pano": True,
            "image_prompt": {
                "source": "media_asset",
                "media_asset_id": media_asset_id,
            },
        },
    }


def parse_world(operation: dict) -> dict:
    world = operation.get("response") or {}
    metadata = operation.get("metadata") or {}
    world_id = world.get("world_id") or world.get("id") or metadata.get("world_id")
    marble = world.get("world_marble_url")
    if not marble and world_id:
        marble = f"https://marble.worldlabs.ai/world/{world_id}"
    assets = world.get("assets") or {}
    splats = (assets.get("splats") or {}).get("spz_urls") or {}
    splat = splats.get("full_res") or splats.get("500k") or splats.get("100k")
    mesh = (assets.get("mesh") or {}).get("collider_mesh_url")
    imagery = (assets.get("imagery") or {}).get("pano_url")
    return {
        "world_id": world_id,
        "marble_url": marble,
        "splat_url": splat,
        "mesh_url": mesh,
        "thumbnail_url": assets.get("thumbnail_url"),
        "imagery_url": imagery,
        "provider": "worldlabs",
        "operation_id": operation.get("operation_id"),
    }


async def generate_world(image_bytes: bytes, display_name: str) -> dict:
    settings = get_settings()
    if not settings.worldlabs_api_key:
        result = await mock_generate_3d("memory")
        return result

    headers = {"WLT-Api-Key": settings.worldlabs_api_key}
    timeout = httpx.Timeout(60.0, read=180.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        prepared = await client.post(
            f"{WORLDLABS_BASE}/marble/v1/media-assets:prepare_upload",
            headers={**headers, "Content-Type": "application/json"},
            json={"file_name": "panorama.jpg", "kind": "image", "extension": "jpg"},
        )
        if prepared.status_code >= 400:
            raise RuntimeError(f"World Labs could not prepare the upload ({prepared.status_code})")
        payload = prepared.json()
        media_id = (payload.get("media_asset") or {}).get("media_asset_id")
        upload = payload.get("upload_info") or {}
        if not media_id or not upload.get("upload_url"):
            raise RuntimeError("World Labs did not return an upload URL")

        put_headers = dict(upload.get("required_headers") or {})
        method = (upload.get("upload_method") or "PUT").upper()
        uploaded = await client.request(method, upload["upload_url"], content=image_bytes, headers=put_headers)
        if uploaded.status_code >= 400:
            raise RuntimeError(f"World Labs upload failed ({uploaded.status_code})")

        body = build_world_request(display_name, media_id, settings.worldlabs_model, settings.worldlabs_public)
        started = await client.post(
            f"{WORLDLABS_BASE}/marble/v1/worlds:generate",
            headers={**headers, "Content-Type": "application/json"},
            json=body,
        )
        if started.status_code == 402:
            raise RuntimeError("World Labs account is out of credits")
        if started.status_code >= 400:
            raise RuntimeError(f"World Labs rejected world generation ({started.status_code})")
        operation = started.json()
        operation_id = operation.get("operation_id")
        if not operation_id:
            raise RuntimeError("World Labs did not return an operation id")

        deadline = time.monotonic() + 20 * 60
        while not operation.get("done"):
            if time.monotonic() > deadline:
                raise RuntimeError("World Labs generation timed out")
            await asyncio.sleep(5)
            polled = await client.get(f"{WORLDLABS_BASE}/marble/v1/operations/{operation_id}", headers=headers)
            if polled.status_code >= 400:
                raise RuntimeError(f"World Labs status check failed ({polled.status_code})")
            operation = polled.json()

        if operation.get("error"):
            err = operation["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(message or "World Labs generation failed")
        return parse_world(operation)
