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
# Draft worlds finish in seconds. Full worlds have taken about 9 minutes,
# so the poll loop waits at least 15 and keeps a few minutes of headroom.
DRAFT_WORLD_TIMEOUT_S = 4 * 60
FULL_WORLD_TIMEOUT_S = 20 * 60


def generation_models(draft_model: str | None, full_model: str) -> list[str]:
    """Draft first when configured, then the full Marble model.

    marble-1.0-draft is the fast preview (about 20 seconds). marble-1.1 is the
    full world (about 5 minutes). An empty draft model skips the preview.
    """
    if draft_model and draft_model != full_model:
        return [draft_model, full_model]
    return [full_model]


def world_display_name(project_id: str, *, staged: bool) -> str:
    """Marble title. Uses the project id so the user's room name is not sent."""
    name = project_id if not staged else f"{project_id}-staged"
    return name[:64]


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


async def generate_world(image_bytes: bytes, display_name: str, on_preview=None) -> dict:
    """Generate a draft world first, then the full world from the same panorama."""
    settings = get_settings()
    models = generation_models(settings.worldlabs_draft_model, settings.worldlabs_model)
    if not settings.worldlabs_api_key:
        result = await mock_generate_3d("memory")
        if len(models) > 1 and on_preview is not None:
            await on_preview(dict(result))
        return result

    headers = {"WLT-Api-Key": settings.worldlabs_api_key}
    timeout = httpx.Timeout(60.0, read=180.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        media_id = await _upload_panorama(client, headers, image_bytes)
        final = None
        for index, model in enumerate(models):
            timeout_s = DRAFT_WORLD_TIMEOUT_S if index < len(models) - 1 else FULL_WORLD_TIMEOUT_S
            try:
                parsed = await _generate_model(
                    client, headers, display_name, media_id, model, settings.worldlabs_public, timeout_s
                )
            except Exception:
                if index < len(models) - 1:
                    logger.exception("Draft world failed; continuing with the full model")
                    continue
                raise
            if index < len(models) - 1:
                if on_preview is not None:
                    await on_preview(parsed)
            else:
                final = parsed
        if final is None:
            raise RuntimeError("World Labs did not return a world")
        return final


async def delete_worlds(world_ids: list[str | None]) -> None:
    """Ask Marble to delete worlds this API created.

    A missing key, a mock world, or a World Labs error does not block deleting
    the local project. 404 means the world is already gone.
    """
    settings = get_settings()
    ids = list(dict.fromkeys(item for item in world_ids if item))
    if not ids or not settings.worldlabs_api_key:
        return
    headers = {"WLT-Api-Key": settings.worldlabs_api_key}
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for world_id in ids:
            url = f"{WORLDLABS_BASE}/marble/v1/worlds/{world_id}"
            try:
                response = await client.delete(url, headers=headers)
            except Exception:
                logger.exception("Could not delete Marble world %s", world_id)
                continue
            if response.status_code in {200, 204, 404}:
                continue
            logger.error("Marble delete for %s returned %s", world_id, response.status_code)


async def _upload_panorama(client: httpx.AsyncClient, headers: dict, image_bytes: bytes) -> str:
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
    return media_id


async def _generate_model(
    client: httpx.AsyncClient,
    headers: dict,
    display_name: str,
    media_id: str,
    model: str,
    public: bool,
    timeout_s: int,
) -> dict:
    body = build_world_request(display_name, media_id, model, public)
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

    deadline = time.monotonic() + timeout_s
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
    parsed = parse_world(operation)
    parsed["model"] = model
    return parsed
