"""World Labs Marble generation. Uses the mock when WORLDLABS_API_KEY is unset."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from config import get_settings
from db import (
    due_draft_cleanups,
    forget_draft_cleanup,
    forget_world_delete,
    note_draft_cleanup_attempt,
    pending_world_delete_ids,
    queue_world_deletes,
    remember_draft_cleanup,
)
from services.world_urls import valid_marble_url, valid_upload_url, valid_world_id
from services.world_gen_mock import mock_generate_3d

logger = logging.getLogger(__name__)

WORLDLABS_BASE = "https://api.worldlabs.ai"
# Draft worlds finish in seconds. Full worlds have taken about 9 minutes,
# so the poll loop waits at least 15 and keeps a few minutes of headroom.
DRAFT_WORLD_TIMEOUT_S = 4 * 60
FULL_WORLD_TIMEOUT_S = 20 * 60

WORLD_FAILED_MESSAGE = "3D generation failed. Try again."
WORLD_CREDITS_MESSAGE = "3D generation is unavailable right now (provider credits)."
WORLD_DELAYED_MESSAGE = "Your 3D tour is delayed, we'll finish it shortly"
WORLD_DELAY_EXPIRED_MESSAGE = "Your 3D tour could not be finished in time. Try again from the app."
WORLD_PROVIDER_QUOTA_MESSAGE = "3D generation is unavailable right now (provider quota)."
WORLD_RATE_LIMIT_EXHAUSTED = (
    "3D generation is unavailable right now (provider quota). Try again from the app."
)

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


class RateLimitExceeded(ProviderFailure):
    """The job used its whole World Labs 429 budget. Callers fail it."""

    def __init__(self, detail: str) -> None:
        super().__init__(WORLD_RATE_LIMIT_EXHAUSTED, detail, 429)


class OperationInFlight(ProviderFailure):
    """A worlds:generate call was accepted, then a later request was rate limited.

    The operation id has already been charged. Callers poll it instead of
    submitting worlds:generate again.
    """

    def __init__(self, operation_id: str, phase: str, detail: str) -> None:
        super().__init__(WORLD_PROVIDER_QUOTA_MESSAGE, detail, 429)
        self.operation_id = operation_id
        self.phase = phase


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


def retry_wait_seconds(attempt: int, retry_after: float | None) -> float:
    """Seconds to wait after attempt ``attempt`` (1 = the first HTTP 429).

    The wait is exponential in the attempt, with full jitter. A Retry-After
    value is a floor: the wait is never shorter than that header, and never
    longer than ``WORLDLABS_RETRY_MAX_SECONDS``.
    """
    settings = get_settings()
    cap = settings.worldlabs_retry_max_seconds
    base = settings.worldlabs_retry_base_seconds
    expo = min(cap, base * (2 ** max(attempt - 1, 0)))
    jittered = random.uniform(0.0, expo) if expo > 0 else 0.0
    if retry_after is None:
        return jittered
    return min(cap, max(jittered, retry_after))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


# Set by the job runner. Counts every HTTP 429 for one job, across polls and
# generate calls, and raises RateLimitExceeded when the job's budget is spent.
_rate_limit_hook: ContextVar = ContextVar("worldlabs_rate_limit_hook", default=None)


def bind_rate_limit_hook(hook):
    return _rate_limit_hook.set(hook)


def reset_rate_limit_hook(token) -> None:
    _rate_limit_hook.reset(token)


def _note_rate_limit() -> None:
    hook = _rate_limit_hook.get()
    if hook is not None:
        hook()


async def _send_retrying_rate_limit(send):
    """Call ``send`` again after HTTP 429, up to the configured attempt cap.

    ``send`` must perform one HTTP request. A non-429 response is returned
    immediately so a charged call is not repeated for any other status.
    Every 429 also counts against the job's total attempt and time budget.
    """
    attempts = get_settings().worldlabs_retry_attempts
    response = None
    for attempt in range(1, attempts + 1):
        response = await send()
        if response.status_code != 429:
            return response
        _note_rate_limit()
        if attempt == attempts:
            return response
        delay = retry_wait_seconds(attempt, _retry_after_seconds(response))
        logger.warning(
            "World Labs returned HTTP 429; waiting %.2fs before attempt %s of %s",
            delay,
            attempt + 1,
            attempts,
        )
        await asyncio.sleep(delay)
    return response


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
    """Remember a zero balance so the next attempt waits instead of calling generate."""
    global _credit_cache
    _credit_cache = {"at": time.monotonic(), "remaining": 0.0}
    _warn_if_low(0.0)


def _warn_if_low(remaining: float) -> None:
    threshold = get_settings().worldlabs_low_credit_threshold
    if remaining < threshold:
        logger.warning(
            "World Labs credit balance is low: %s remaining (threshold %s)",
            remaining,
            threshold,
        )


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
    value = float(remaining)
    _warn_if_low(value)
    return value


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
    world_id = valid_world_id(world.get("world_id") or world.get("id") or metadata.get("world_id"))
    marble = valid_marble_url(world.get("world_marble_url"))
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
    on_tick=None,
) -> dict:
    """Generate a draft world first, then the full world from the same panorama.

    ``on_submitting`` runs immediately before each worlds:generate call.
    ``on_operation`` runs as soon as World Labs returns an operation id, before
    the poll loop, so a restart can re-poll that id instead of generating again.
    """
    settings = get_settings()
    models = generation_models(settings.worldlabs_draft_model, settings.worldlabs_model)
    if not settings.worldlabs_api_key:
        return await _mock_world(
            models,
            on_preview=on_preview,
            on_operation=on_operation,
            on_submitting=on_submitting,
            on_tick=on_tick,
        )

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
            on_tick=on_tick,
        )


async def poll_world_operation(operation_id: str, timeout_s: int, on_tick=None) -> dict:
    """Poll an operation World Labs already accepted. Does not call worlds:generate."""
    settings = get_settings()
    if not settings.worldlabs_api_key:
        if on_tick is not None:
            on_tick()
        result = await mock_generate_3d("memory")
        result["operation_id"] = operation_id
        return result
    headers = {"WLT-Api-Key": settings.worldlabs_api_key}
    timeout = httpx.Timeout(60.0, read=180.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        operation = await _poll_until_done(client, headers, operation_id, timeout_s, on_tick=on_tick)
    _raise_operation_error(operation)
    return parse_world(operation)


async def continue_full_world(
    image_bytes: bytes,
    display_name: str,
    media_asset_id: str | None,
    on_operation=None,
    on_submitting=None,
    on_tick=None,
) -> dict:
    """Start the full model after a resumed draft. Does not regenerate the draft."""
    settings = get_settings()
    model = settings.worldlabs_model
    if not settings.worldlabs_api_key:
        return await _mock_world([model], on_operation=on_operation, on_submitting=on_submitting, on_tick=on_tick)
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
            on_tick=on_tick,
        )


async def _mock_world(models: list[str], on_preview=None, on_operation=None, on_submitting=None, on_tick=None) -> dict:
    if on_tick is not None:
        on_tick()
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
    on_tick=None,
) -> dict:
    final = None
    draft_operation_id: str | None = None

    async def _remember_operation(operation_id: str, remembered_phase: str) -> None:
        nonlocal draft_operation_id
        if remembered_phase == "draft":
            draft_operation_id = operation_id
        if on_operation is not None:
            await on_operation(operation_id, remembered_phase)

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
                on_operation=_remember_operation,
                on_submitting=on_submitting,
                on_tick=on_tick,
            )
        except ProviderFailure as exc:
            if isinstance(exc, OperationInFlight) or exc.status_code == 429:
                raise
            if phase == "draft" and not _is_out_of_credits(exc):
                logger.error("Draft world failed (%s); continuing with the full model", exc.detail)
                await _abandon_draft_operation(client, headers, draft_operation_id)
                continue
            raise
        except Exception:
            if phase == "draft":
                logger.exception("Draft world failed; continuing with the full model")
                await _abandon_draft_operation(client, headers, draft_operation_id)
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
    ids = list(dict.fromkeys(valid_world_id(item) for item in world_ids if valid_world_id(item)))
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


# A draft finishes in seconds. Stop polling an operation that never settles.
_DRAFT_CLEANUP_ATTEMPTS = 24


async def retry_pending_world_deletes(*, force: bool = False) -> int:
    """Retry Marble deletes, and poll draft operations that were left behind.

    The draft poll is a GET. It does not call worlds:generate.
    """
    settings = get_settings()
    if not settings.worldlabs_api_key:
        return 0
    ids = pending_world_delete_ids(force=force)
    cleanups = due_draft_cleanups(force=force)
    if not ids and not cleanups:
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
        await _settle_draft_cleanups(client, headers, cleanups)
    return cleared


async def _delete_one(client: httpx.AsyncClient, headers: dict, world_id: str) -> tuple[bool, str | None]:
    if valid_world_id(world_id) is None:
        return False, "invalid world id"
    url = f"{WORLDLABS_BASE}/marble/v1/worlds/{world_id}"
    try:
        response = await client.delete(url, headers=headers)
    except Exception:
        logger.exception("Could not delete Marble world %s", world_id)
        return False, "request failed"
    if response.status_code in {200, 204, 404}:
        logger.info("Deleted Marble world %s", world_id)
        return True, None
    return False, f"HTTP {response.status_code}"


async def _upload_panorama(client: httpx.AsyncClient, headers: dict, image_bytes: bytes) -> str:
    prepared = await _send_retrying_rate_limit(
        lambda: client.post(
            f"{WORLDLABS_BASE}/marble/v1/media-assets:prepare_upload",
            headers={**headers, "Content-Type": "application/json"},
            json={"file_name": "panorama.jpg", "kind": "image", "extension": "jpg"},
        )
    )
    _raise_for_status(prepared, "World Labs could not prepare the upload")
    payload = prepared.json()
    media_id = (payload.get("media_asset") or {}).get("media_asset_id")
    upload = payload.get("upload_info") or {}
    upload_url = valid_upload_url(upload.get("upload_url"))
    method = str(upload.get("upload_method") or "PUT").upper()
    if not media_id or upload_url is None or method not in {"PUT", "POST"}:
        raise ProviderFailure(WORLD_FAILED_MESSAGE, "World Labs returned an upload URL that was not accepted")
    put_headers = dict(upload.get("required_headers") or {})
    uploaded = await _send_retrying_rate_limit(
        lambda: client.request(method, upload_url, content=image_bytes, headers=put_headers)
    )
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
    on_tick=None,
) -> dict:
    body = build_world_request(display_name, media_id, model, public)
    await _require_credits(model)
    if on_submitting is not None:
        await on_submitting(phase)
    started = await _send_retrying_rate_limit(
        lambda: client.post(
            f"{WORLDLABS_BASE}/marble/v1/worlds:generate",
            headers={**headers, "Content-Type": "application/json"},
            json=body,
        )
    )
    _raise_for_status(started, "World Labs rejected world generation")
    operation = started.json()
    operation_id = valid_world_id(operation.get("operation_id"))
    if operation_id is None:
        raise ProviderFailure(WORLD_FAILED_MESSAGE, "World Labs returned an operation id that was not accepted")
    if on_operation is not None:
        await on_operation(operation_id, phase)
    clear_world_credits_cache()
    operation = await _poll_until_done(
        client, headers, operation_id, timeout_s, operation, on_tick=on_tick, phase=phase
    )
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
    on_tick=None,
    phase: str = "full",
) -> dict:
    operation_id = valid_world_id(operation_id) or ""
    if not operation_id:
        raise ProviderFailure(WORLD_FAILED_MESSAGE, "World Labs returned an operation id that was not accepted")
    operation = operation or {"operation_id": operation_id, "done": False}
    deadline = time.monotonic() + timeout_s
    while not operation.get("done"):
        if on_tick is not None:
            on_tick()
        if time.monotonic() > deadline:
            raise ProviderFailure(WORLD_FAILED_MESSAGE, f"HTTP timeout: operation {operation_id} exceeded {timeout_s}s")
        polled = await _send_retrying_rate_limit(
            lambda: client.get(f"{WORLDLABS_BASE}/marble/v1/operations/{operation_id}", headers=headers)
        )
        if polled.status_code == 429:
            message = _response_message(polled) or "rate limited"
            raise OperationInFlight(operation_id, phase, provider_detail(429, message))
        _raise_for_status(polled, "World Labs status check failed")
        operation = polled.json()
        if not operation.get("done"):
            await asyncio.sleep(5)
    return operation


async def _abandon_draft_operation(
    client: httpx.AsyncClient, headers: dict, operation_id: str | None
) -> None:
    """One free GET. A finished world is queued for delete; a live one is remembered.

    This never calls worlds:generate and never raises into the full-model attempt.
    """
    try:
        checked = valid_world_id(operation_id)
        if checked is None:
            return
        response = await client.get(f"{WORLDLABS_BASE}/marble/v1/operations/{checked}", headers=headers)
        if response.status_code >= 400:
            remember_draft_cleanup(checked)
            return
        body = response.json()
        operation = body if isinstance(body, dict) else {}
        world_id = parse_world(operation).get("world_id")
        if world_id:
            queue_world_deletes([world_id], "orphaned draft")
            forget_draft_cleanup(checked)
            return
        if operation.get("done"):
            forget_draft_cleanup(checked)
            return
        remember_draft_cleanup(checked)
    except Exception:
        logger.exception("Could not check orphaned draft operation %s", operation_id)
        try:
            if valid_world_id(operation_id):
                remember_draft_cleanup(str(operation_id))
        except Exception:
            logger.exception("Could not queue orphaned draft operation %s", operation_id)


async def _settle_draft_cleanups(
    client: httpx.AsyncClient,
    headers: dict,
    rows: list[tuple[str, int]],
) -> None:
    for operation_id, attempts in rows:
        if attempts >= _DRAFT_CLEANUP_ATTEMPTS:
            logger.error("Giving up on orphaned draft operation %s", operation_id)
            forget_draft_cleanup(operation_id)
            continue
        checked = valid_world_id(operation_id)
        if checked is None:
            forget_draft_cleanup(operation_id)
            continue
        try:
            response = await client.get(
                f"{WORLDLABS_BASE}/marble/v1/operations/{checked}", headers=headers
            )
        except Exception:
            logger.exception("Could not poll orphaned draft operation %s", checked)
            note_draft_cleanup_attempt(checked)
            continue
        if response.status_code >= 400:
            note_draft_cleanup_attempt(checked)
            continue
        try:
            body = response.json()
        except Exception:
            note_draft_cleanup_attempt(checked)
            continue
        operation = body if isinstance(body, dict) else {}
        world_id = parse_world(operation).get("world_id")
        if world_id:
            queue_world_deletes([world_id], "orphaned draft")
            forget_draft_cleanup(checked)
            continue
        if operation.get("done"):
            forget_draft_cleanup(checked)
            continue
        note_draft_cleanup_attempt(checked)


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
