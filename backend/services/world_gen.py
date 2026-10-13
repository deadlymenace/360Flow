"""World Labs Marble generation. Uses the mock when WORLDLABS_API_KEY is unset."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from config import get_settings
from db import forget_world_delete, pending_world_delete_ids, queue_world_deletes
from services.world_gen_mock import mock_generate_3d

logger = logging.getLogger(__name__)

WORLDLABS_BASE = "https://api.worldlabs.ai"
# Draft worlds finish in seconds. Full worlds have taken about 9 minutes,
# so the poll loop waits at least 15 and keeps a few minutes of headroom.
DRAFT_WORLD_TIMEOUT_S = 4 * 60
FULL_WORLD_TIMEOUT_S = 20 * 60

WORLD_FAILED_MESSAGE = "3D generation failed. Try again."
WORLD_CREDITS_MESSAGE = "3D generation is unavailable right now (provider credits)."
WORLD_PROVIDER_QUOTA_MESSAGE = "3D generation is unavailable right now (provider quota)."

# Pano world generation. Draft is 150; marble-1.0 / marble-1.1 are 1,500.
# A plus model can cost more, so 1,500 is only the minimum that must be on hand.
DRAFT_WORLD_CREDITS = 150
FULL_WORLD_CREDITS = 1500
CREDIT_CACHE_TTL_S = 30.0

_credit_cache: dict | None = None


class ProviderFailure(Exception):
    """Provider error with a client-safe sentence and an internal detail string."""

    def __init__(self, client_message: str, detail: str, status_code: int | None = None) -> None:
        super().__init__(client_message)
        self.client_message = client_message
        self.detail = " ".join(detail.split())[:500]
        self.status_code = status_code


def world_client_message(status_code: int | None, message: str) -> str:
    """Map credit and quota failures to a specific sentence. Everything else stays generic."""
    text = (message or "").lower()
    if status_code == 402 or any(token in text for token in ("credit", "billing", "payment required")):
        return WORLD_CREDITS_MESSAGE
    if status_code == 429 or any(
        token in text
        for token in ("quota", "rate limit", "rate_limit", "too many requests", "resource exhausted", "resource_exhausted")
    ):
        return WORLD_PROVIDER_QUOTA_MESSAGE
    return WORLD_FAILED_MESSAGE


def provider_detail(status_code: int | None, message: str) -> str:
    text = " ".join((message or "request failed").split())[:400]
    if status_code is None:
        return text
    return f"HTTP {status_code}: {text}"


def credits_required(model: str) -> int:
    """Minimum credits for one world. Pano input adds no extra pano-generation charge."""
    if "draft" in model:
        return DRAFT_WORLD_CREDITS
    return FULL_WORLD_CREDITS


def clear_world_credits_cache() -> None:
    global _credit_cache
    _credit_cache = None


def note_out_of_credits() -> None:
    """Remember a zero balance so the next job fails before it calls generate."""
    global _credit_cache
    _credit_cache = {"at": time.monotonic(), "remaining": 0.0}


def _is_out_of_credits(exc: ProviderFailure) -> bool:
    return exc.status_code == 402 or exc.client_message == WORLD_CREDITS_MESSAGE


async def world_credits_balance() -> float | None:
    """Remaining World Labs credits.

    The value is cached for ``CREDIT_CACHE_TTL_S``. None means the key is unset
    or the credits call failed; callers then let generation try and rely on a
    402 to stop.
    """
    global _credit_cache
    settings = get_settings()
    if not settings.worldlabs_api_key:
        return None
    now = time.monotonic()
    if _credit_cache is not None and now - float(_credit_cache["at"]) < CREDIT_CACHE_TTL_S:
        return _credit_cache["remaining"]
    remaining = await _fetch_credits(settings.worldlabs_api_key)
    _credit_cache = {"at": now, "remaining": remaining}
    return remaining


async def _fetch_credits(api_key: str) -> float | None:
    timeout = httpx.Timeout(5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{WORLDLABS_BASE}/marble/v1/credits",
                headers={"WLT-Api-Key": api_key},
            )
    except Exception:
        logger.warning("World Labs credits check failed", exc_info=True)
        return None
    if response.status_code >= 400:
        logger.warning("World Labs credits check returned HTTP %s", response.status_code)
        return None
    try:
        body = response.json()
    except Exception:
        logger.warning("World Labs credits check returned a body that is not JSON")
        return None
    remaining = body.get("remaining_credits") if isinstance(body, dict) else None
    if not isinstance(remaining, (int, float)):
        logger.warning("World Labs credits check did not include remaining_credits")
        return None
    logger.info("World Labs credits remaining: %s", remaining)
    return float(remaining)


async def _require_credits(model: str) -> None:
    """Stop before worlds:generate when the cached balance cannot pay for this model."""
    remaining = await world_credits_balance()
    if remaining is None:
        return
    needed = credits_required(model)
    if remaining >= needed:
        return
    detail = f"out of credits (remaining {remaining:g}, {model} needs {needed})"
    logger.error("World Labs %s", detail)
    raise ProviderFailure(WORLD_CREDITS_MESSAGE, detail, 402)


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


async def generate_world(
    image_bytes: bytes,
    display_name: str,
    on_preview=None,
    on_media=None,
    on_operation=None,
    on_submitting=None,
) -> dict:
    """Generate a draft world first, then the full world from the same panorama.

    ``on_submitting`` runs immediately before each worlds:generate call.
    ``on_operation`` runs as soon as World Labs returns an operation id, before
    the poll loop, so a restart can re-poll that id instead of generating again.
    """
    settings = get_settings()
    models = generation_models(settings.worldlabs_draft_model, settings.worldlabs_model)
    if not settings.worldlabs_api_key:
        return await _mock_world(models, on_preview=on_preview, on_operation=on_operation, on_submitting=on_submitting)

    headers = {"WLT-Api-Key": settings.worldlabs_api_key}
    timeout = httpx.Timeout(60.0, read=180.0)
    await _require_credits(models[0])
    async with httpx.AsyncClient(timeout=timeout) as client:
        media_id = await _upload_panorama(client, headers, image_bytes)
        if on_media is not None:
            await on_media(media_id)
        return await _generate_models(
            client,
            headers,
            display_name,
            media_id,
            models,
            settings.worldlabs_public,
            on_preview=on_preview,
            on_operation=on_operation,
            on_submitting=on_submitting,
        )


async def poll_world_operation(operation_id: str, timeout_s: int) -> dict:
    """Poll an operation World Labs already accepted. Does not call worlds:generate."""
    settings = get_settings()
    if not settings.worldlabs_api_key:
        result = await mock_generate_3d("memory")
        result["operation_id"] = operation_id
        return result
    headers = {"WLT-Api-Key": settings.worldlabs_api_key}
    timeout = httpx.Timeout(60.0, read=180.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        operation = await _poll_until_done(client, headers, operation_id, timeout_s)
    _raise_operation_error(operation)
    return parse_world(operation)


async def continue_full_world(
    image_bytes: bytes,
    display_name: str,
    media_asset_id: str | None,
    on_operation=None,
    on_submitting=None,
) -> dict:
    """Start the full model after a resumed draft. Does not regenerate the draft."""
    settings = get_settings()
    model = settings.worldlabs_model
    if not settings.worldlabs_api_key:
        return await _mock_world([model], on_operation=on_operation, on_submitting=on_submitting)
    headers = {"WLT-Api-Key": settings.worldlabs_api_key}
    timeout = httpx.Timeout(60.0, read=180.0)
    await _require_credits(model)
    async with httpx.AsyncClient(timeout=timeout) as client:
        media_id = media_asset_id or await _upload_panorama(client, headers, image_bytes)
        return await _generate_model(
            client,
            headers,
            display_name,
            media_id,
            model,
            settings.worldlabs_public,
            FULL_WORLD_TIMEOUT_S,
            phase="full",
            on_operation=on_operation,
            on_submitting=on_submitting,
        )


async def _mock_world(models: list[str], on_preview=None, on_operation=None, on_submitting=None) -> dict:
    if on_submitting is not None:
        await on_submitting("draft" if len(models) > 1 else "full")
    if on_operation is not None:
        await on_operation("mock-operation", "draft" if len(models) > 1 else "full")
    result = await mock_generate_3d("memory")
    if len(models) > 1 and on_preview is not None:
        await on_preview(dict(result))
        if on_submitting is not None:
            await on_submitting("full")
        if on_operation is not None:
            await on_operation("mock-operation-full", "full")
    result["model"] = models[-1]
    return result


async def _generate_models(
    client: httpx.AsyncClient,
    headers: dict,
    display_name: str,
    media_id: str,
    models: list[str],
    public: bool,
    on_preview=None,
    on_operation=None,
    on_submitting=None,
) -> dict:
    final = None
    for index, model in enumerate(models):
        phase = "full" if index == len(models) - 1 else "draft"
        timeout_s = DRAFT_WORLD_TIMEOUT_S if phase == "draft" else FULL_WORLD_TIMEOUT_S
        try:
            parsed = await _generate_model(
                client,
                headers,
                display_name,
                media_id,
                model,
                public,
                timeout_s,
                phase=phase,
                on_operation=on_operation,
                on_submitting=on_submitting,
            )
        except ProviderFailure as exc:
            if phase == "draft" and not _is_out_of_credits(exc):
                logger.error("Draft world failed (%s); continuing with the full model", exc.detail)
                continue
            raise
        except Exception:
            if phase == "draft":
                logger.exception("Draft world failed; continuing with the full model")
                continue
            raise
        if phase == "draft":
            if on_preview is not None:
                await on_preview(parsed)
        else:
            final = parsed
    if final is None:
        raise ProviderFailure(WORLD_FAILED_MESSAGE, "World Labs did not return a world")
    return final


async def delete_worlds(world_ids: list[str | None]) -> None:
    """Ask Marble to delete worlds this API created.

    A missing key or a mock world is skipped. A World Labs error does not block
    deleting the local project; the world id is queued and retried. 404 means
    the world is already gone.
    """
    settings = get_settings()
    ids = list(dict.fromkeys(item for item in world_ids if item))
    if not ids or not settings.worldlabs_api_key:
        return
    headers = {"WLT-Api-Key": settings.worldlabs_api_key}
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for world_id in ids:
            ok, error = await _delete_one(client, headers, world_id)
            if ok:
                forget_world_delete(world_id)
                continue
            logger.error("Marble delete for %s failed: %s", world_id, error)
            queue_world_deletes([world_id], error or "delete failed")


async def retry_pending_world_deletes(*, force: bool = False) -> int:
    """Retry Marble deletes that failed earlier. Returns how many are now gone."""
    settings = get_settings()
    ids = pending_world_delete_ids(force=force)
    if not ids or not settings.worldlabs_api_key:
        return 0
    headers = {"WLT-Api-Key": settings.worldlabs_api_key}
    cleared = 0
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for world_id in ids:
            ok, error = await _delete_one(client, headers, world_id)
            if ok:
                forget_world_delete(world_id)
                cleared += 1
                continue
            logger.error("Marble delete retry for %s failed: %s", world_id, error)
            queue_world_deletes([world_id], error or "delete failed")
    return cleared


async def _delete_one(client: httpx.AsyncClient, headers: dict, world_id: str) -> tuple[bool, str | None]:
    url = f"{WORLDLABS_BASE}/marble/v1/worlds/{world_id}"
    try:
        response = await client.delete(url, headers=headers)
    except Exception:
        logger.exception("Could not delete Marble world %s", world_id)
        return False, "request failed"
    if response.status_code in {200, 204, 404}:
        return True, None
    return False, f"HTTP {response.status_code}"


async def _upload_panorama(client: httpx.AsyncClient, headers: dict, image_bytes: bytes) -> str:
    prepared = await client.post(
        f"{WORLDLABS_BASE}/marble/v1/media-assets:prepare_upload",
        headers={**headers, "Content-Type": "application/json"},
        json={"file_name": "panorama.jpg", "kind": "image", "extension": "jpg"},
    )
    _raise_for_status(prepared, "World Labs could not prepare the upload")
    payload = prepared.json()
    media_id = (payload.get("media_asset") or {}).get("media_asset_id")
    upload = payload.get("upload_info") or {}
    if not media_id or not upload.get("upload_url"):
        raise ProviderFailure(WORLD_FAILED_MESSAGE, "World Labs did not return an upload URL")
    put_headers = dict(upload.get("required_headers") or {})
    method = (upload.get("upload_method") or "PUT").upper()
    uploaded = await client.request(method, upload["upload_url"], content=image_bytes, headers=put_headers)
    _raise_for_status(uploaded, "World Labs upload failed")
    return media_id


async def _generate_model(
    client: httpx.AsyncClient,
    headers: dict,
    display_name: str,
    media_id: str,
    model: str,
    public: bool,
    timeout_s: int,
    phase: str = "full",
    on_operation=None,
    on_submitting=None,
) -> dict:
    body = build_world_request(display_name, media_id, model, public)
    await _require_credits(model)
    if on_submitting is not None:
        await on_submitting(phase)
    started = await client.post(
        f"{WORLDLABS_BASE}/marble/v1/worlds:generate",
        headers={**headers, "Content-Type": "application/json"},
        json=body,
    )
    _raise_for_status(started, "World Labs rejected world generation")
    operation = started.json()
    operation_id = operation.get("operation_id")
    if not operation_id:
        raise ProviderFailure(WORLD_FAILED_MESSAGE, "World Labs did not return an operation id")
    if on_operation is not None:
        await on_operation(operation_id, phase)
    clear_world_credits_cache()
    operation = await _poll_until_done(client, headers, operation_id, timeout_s, operation)
    _raise_operation_error(operation)
    parsed = parse_world(operation)
    parsed["model"] = model
    return parsed


async def _poll_until_done(
    client: httpx.AsyncClient,
    headers: dict,
    operation_id: str,
    timeout_s: int,
    operation: dict | None = None,
) -> dict:
    operation = operation or {"operation_id": operation_id, "done": False}
    deadline = time.monotonic() + timeout_s
    while not operation.get("done"):
        if time.monotonic() > deadline:
            raise ProviderFailure(WORLD_FAILED_MESSAGE, f"HTTP timeout: operation {operation_id} exceeded {timeout_s}s")
        polled = await client.get(f"{WORLDLABS_BASE}/marble/v1/operations/{operation_id}", headers=headers)
        _raise_for_status(polled, "World Labs status check failed")
        operation = polled.json()
        if not operation.get("done"):
            await asyncio.sleep(5)
    return operation


def _raise_for_status(response: httpx.Response, fallback: str) -> None:
    if response.status_code < 400:
        return
    message = _response_message(response) or fallback
    detail = provider_detail(response.status_code, message)
    if response.status_code == 402 or world_client_message(response.status_code, message) == WORLD_CREDITS_MESSAGE:
        note_out_of_credits()
        detail = f"out of credits: {detail}"
    logger.error("World Labs request failed: %s", detail)
    raise ProviderFailure(world_client_message(response.status_code, message), detail, response.status_code)


def _raise_operation_error(operation: dict) -> None:
    err = operation.get("error")
    if not err:
        return
    message = err.get("message") if isinstance(err, dict) else str(err)
    code = err.get("code") if isinstance(err, dict) else None
    status = code if isinstance(code, int) and code >= 400 else None
    text = message or "World Labs generation failed"
    client_message = world_client_message(status, text)
    detail = provider_detail(status, text)
    if client_message == WORLD_CREDITS_MESSAGE:
        note_out_of_credits()
        detail = f"out of credits: {detail}"
    logger.error("World Labs operation failed: %s", detail)
    raise ProviderFailure(client_message, detail, status)


def _response_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str):
            return error
        if body.get("message"):
            return str(body["message"])
    text = (response.text or "").strip()
    return text[:400]
