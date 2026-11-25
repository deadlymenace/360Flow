"""World Labs Atlas v2 3D generation (replaces legacy Marble v1 API).

Uses equirectangular panorama slicing (services.pano_slicer) and the Atlas
Developer API v2 (services.atlas_client) in draft mode by default. Falls back
to mock_generate_3d only when BYOK_REQUIRED=false and no Atlas key is set.
"""

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
    world_ids_in_use,
)
from services.atlas_client import (
    ATLAS_API_BASE_URL,
    ATLAS_POSE_CREDITS_PER_IMAGE,
    ATLAS_SPLAT_DRAFT_CREDITS,
    ATLAS_SPLAT_REFINED_CREDITS,
    AtlasError,
    AtlasRateLimit,
    extract_splat_asset,
    poll_atlas_operation,
    resolve_read_url,
    run_atlas_pipeline,
)
from services.world_gen_mock import mock_generate_3d
from services.world_urls import valid_marble_url, valid_world_id

logger = logging.getLogger(__name__)

ATLAS_API_BASE = ATLAS_API_BASE_URL
WORLDLABS_BASE = ATLAS_API_BASE_URL

DRAFT_WORLD_TIMEOUT_S = 5 * 60
FULL_WORLD_TIMEOUT_S = 25 * 60

WORLD_FAILED_MESSAGE = "3D generation failed. Try again."
WORLD_CREDITS_MESSAGE = "3D generation is unavailable right now (provider credits)."
WORLD_DELAYED_MESSAGE = "Your 3D tour is delayed, we'll finish it shortly"
WORLD_DELAY_EXPIRED_MESSAGE = "Your 3D tour could not be finished in time. Try again from the app."
WORLD_PROVIDER_QUOTA_MESSAGE = "3D generation is unavailable right now (provider quota)."
WORLD_RATE_LIMIT_EXHAUSTED = (
    "3D generation is unavailable right now (provider quota). Try again from the app."
)

# Atlas credit estimates (1,000 credits = $1.00 USD):
# 10 slices * 2 credits + 500 draft splat = 520 ($0.52); refined = 5,020 ($5.02).
DRAFT_WORLD_CREDITS = ATLAS_SPLAT_DRAFT_CREDITS + 10 * ATLAS_POSE_CREDITS_PER_IMAGE
FULL_WORLD_CREDITS = ATLAS_SPLAT_REFINED_CREDITS + 10 * ATLAS_POSE_CREDITS_PER_IMAGE
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
    """The job used its whole Atlas 429 budget. Callers fail it."""

    def __init__(self, detail: str) -> None:
        super().__init__(WORLD_RATE_LIMIT_EXHAUSTED, detail, 429)


class OperationInFlight(ProviderFailure):
    """An Atlas task was accepted, then a later request was rate limited."""

    def __init__(self, operation_id: str, phase: str, detail: str) -> None:
        super().__init__(WORLD_PROVIDER_QUOTA_MESSAGE, detail, 429)
        self.operation_id = operation_id
        self.phase = phase


class LeaseLost(Exception):
    """The job runner no longer owns this job, so no further paid call may start."""


def world_client_message(status_code: int | None, message: str) -> str:
    text = (message or "").lower()
    if status_code == 402 or any(token in text for token in ("credit", "billing", "payment required", "spend_limit")):
        return WORLD_CREDITS_MESSAGE
    if status_code == 429 or any(
        token in text
        for token in (
            "quota",
            "rate limit",
            "rate_limit",
            "too many requests",
            "resource exhausted",
            "concurrency_limit",
            "task_unavailable",
        )
    ):
        return WORLD_PROVIDER_QUOTA_MESSAGE
    return WORLD_FAILED_MESSAGE


def retry_wait_seconds(attempt: int, retry_after: float | None) -> float:
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


_rate_limit_hook: ContextVar = ContextVar("worldlabs_rate_limit_hook", default=None)
_rate_limit_success_hook: ContextVar = ContextVar("worldlabs_rate_limit_success_hook", default=None)


def bind_rate_limit_hook(hook, on_success=None):
    return (_rate_limit_hook.set(hook), _rate_limit_success_hook.set(on_success))


def reset_rate_limit_hook(token) -> None:
    limit_token, success_token = token
    _rate_limit_hook.reset(limit_token)
    _rate_limit_success_hook.reset(success_token)


def _note_rate_limit() -> None:
    hook = _rate_limit_hook.get()
    if hook is not None:
        hook()


def _note_rate_limit_success() -> None:
    hook = _rate_limit_success_hook.get()
    if hook is not None:
        hook()


async def _send_retrying_rate_limit(send):
    attempts = get_settings().worldlabs_retry_attempts
    response = None
    for attempt in range(1, attempts + 1):
        response = await send()
        if response.status_code != 429:
            if 200 <= response.status_code < 300:
                _note_rate_limit_success()
            return response
        _note_rate_limit()
        if attempt == attempts:
            return response
        delay = retry_wait_seconds(attempt, _retry_after_seconds(response))
        logger.warning(
            "Atlas returned HTTP 429; waiting %.2fs before attempt %s of %s",
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
    if "draft" in model:
        return DRAFT_WORLD_CREDITS
    return FULL_WORLD_CREDITS


def clear_world_credits_cache() -> None:
    global _credit_cache
    _credit_cache = None


def note_out_of_credits() -> None:
    global _credit_cache
    _credit_cache = {"at": time.monotonic(), "remaining": 0.0}
    _warn_if_low(0.0)


def _warn_if_low(remaining: float) -> None:
    threshold = get_settings().worldlabs_low_credit_threshold
    if remaining < threshold:
        logger.warning(
            "Atlas credit balance is low: %s remaining (threshold %s)",
            remaining,
            threshold,
        )


def _is_out_of_credits(exc: ProviderFailure) -> bool:
    return exc.status_code == 402 or exc.client_message == WORLD_CREDITS_MESSAGE


async def world_credits_balance(api_key: str | None = None) -> float | None:
    """Atlas v2 has no pre-flight credit balance endpoint (402 is returned on task submission)."""
    return None


async def _require_credits(model: str, api_key: str | None = None) -> None:
    remaining = await (world_credits_balance(api_key) if api_key else world_credits_balance())
    if remaining is None:
        return
    needed = credits_required(model)
    floor = 0 if api_key else get_settings().worldlabs_hard_credit_floor
    if remaining >= needed and (remaining - needed) >= floor:
        return
    detail = f"out of credits (remaining {remaining:g}, {model} needs {needed}, floor {floor})"
    logger.error("Atlas %s", detail)
    raise ProviderFailure(WORLD_CREDITS_MESSAGE, detail, 402)


def generation_models(draft_model: str | None, full_model: str) -> list[str]:
    if draft_model and draft_model != full_model:
        return [draft_model, full_model]
    return [full_model]


def world_display_name(project_id: str, *, staged: bool) -> str:
    name = project_id if not staged else f"{project_id}-staged"
    return name[:64]


def _resolve_world_key(api_key: str | None, require_byok: bool) -> str | None:
    cleaned = (api_key or "").strip() or None
    if require_byok:
        if not cleaned:
            raise ProviderFailure(
                "Atlas API key is required for 3D generation (send X-Atlas-Key).",
                "BYOK Atlas key missing",
                400,
            )
        return cleaned
    return cleaned or get_settings().worldlabs_api_key


def _map_atlas_exc(exc: AtlasError) -> ProviderFailure:
    if exc.status_code == 402:
        note_out_of_credits()
    if isinstance(exc, AtlasRateLimit):
        return ProviderFailure(WORLD_PROVIDER_QUOTA_MESSAGE, exc.detail, 429)
    return ProviderFailure(exc.client_message, exc.detail, exc.status_code)


def smallest_splat_url(spz_urls: dict) -> str | None:
    if not isinstance(spz_urls, dict) or not spz_urls:
        return None
    candidates = []
    for key, val in spz_urls.items():
        url = val.get("url") if isinstance(val, dict) else val
        if isinstance(url, str) and url:
            digits = "".join(ch for ch in str(key) if ch.isdigit())
            rank = int(digits) if digits else (100_000 if "100" in str(key) else 10**9)
            candidates.append((rank, url))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def parse_world(operation: dict) -> dict:
    """Normalize an Atlas operation (or test payload) into 360Flow's world result shape."""
    response = operation.get("response") or {}
    if not isinstance(response, dict):
        response = {}
    asset_id, spz_url = extract_splat_asset(response)
    if spz_url is None:
        assets = response.get('assets') or {}
        spz_url = smallest_splat_url(((assets.get('splats') or {}).get('spz_urls')) or {})
    op_id = valid_world_id(operation.get("id") or operation.get("operation_id"))
    world_id = valid_world_id(response.get("world_id") or asset_id or op_id)
    # Atlas does not expose a hosted Marble viewer page; all rendering uses 360Flow Spark.js
    marble_url = valid_marble_url(response.get("world_marble_url"))
    return {
        "provider": "atlas",
        "world_id": world_id,
        "marble_url": marble_url,
        "splat_url": spz_url,
        "splat_asset_id": asset_id,
        "mesh_url": None,
        "thumbnail_url": None,
        "operation_id": op_id,
    }


async def generate_world(
    image_bytes: bytes,
    display_name: str,
    on_preview=None,
    on_media=None,
    on_operation=None,
    on_submitting=None,
    on_tick=None,
    *,
    api_key: str | None = None,
    require_byok: bool = False,
) -> dict:
    """Slice an equirectangular panorama and generate an Atlas 3D splat (draft by default)."""
    settings = get_settings()
    effective_key = _resolve_world_key(api_key, require_byok)
    if not effective_key:
        models = generation_models(settings.worldlabs_draft_model, settings.worldlabs_model)
        return await _mock_world(
            models,
            on_preview=on_preview,
            on_operation=on_operation,
            on_submitting=on_submitting,
            on_tick=on_tick,
        )

    draft = settings.atlas_draft_mode
    model_name = "atlas-draft" if draft else "atlas-refined"
    byok_key = effective_key if (api_key and api_key.strip()) else None
    await _require_credits(model_name, api_key=byok_key)

    timeout = httpx.Timeout(60.0, read=180.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            result = await run_atlas_pipeline(
                client,
                api_key=effective_key,
                panorama_bytes=image_bytes,
                project_label=display_name,
                base_url=settings.atlas_api_base_url,
                slice_count=settings.atlas_slice_count,
                hfov_deg=settings.atlas_slice_hfov_deg,
                draft=draft,
                synthesize_baseline=settings.atlas_synthesize_baseline,
                pose_timeout_s=DRAFT_WORLD_TIMEOUT_S,
                splat_timeout_s=DRAFT_WORLD_TIMEOUT_S if draft else FULL_WORLD_TIMEOUT_S,
                max_submit_attempts=getattr(settings, "atlas_max_submit_attempts_per_job", 2),
                on_operation=on_operation,
                on_submitting=on_submitting,
                on_tick=on_tick,
            )
            if on_media is not None and result.get("splat_asset_id"):
                await on_media(str(result["splat_asset_id"]))
            return result
    except AtlasError as exc:
        raise _map_atlas_exc(exc) from exc


async def poll_world_operation(
    operation_id: str,
    timeout_s: int,
    on_tick=None,
    *,
    api_key: str | None = None,
    require_byok: bool = False,
) -> dict:
    """Poll an existing Atlas operation without resubmitting tasks."""
    effective_key = _resolve_world_key(api_key, require_byok)
    if not effective_key:
        if on_tick is not None:
            on_tick()
        result = await mock_generate_3d("memory")
        result["operation_id"] = operation_id
        return result

    settings = get_settings()
    timeout = httpx.Timeout(60.0, read=180.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            op = await poll_atlas_operation(
                client,
                settings.atlas_api_base_url.rstrip("/"),
                effective_key,
                operation_id,
                timeout_s,
                on_tick=on_tick,
            )
            parsed = parse_world(op)
            resolved = await resolve_read_url(
                client,
                settings.atlas_api_base_url.rstrip("/"),
                effective_key,
                parsed.get("splat_asset_id"),
                parsed.get("splat_url"),
            )
            if resolved:
                parsed["splat_url"] = resolved
            return parsed
    except AtlasError as exc:
        raise _map_atlas_exc(exc) from exc


async def continue_full_world(
    image_bytes: bytes,
    display_name: str,
    media_asset_id: str | None,
    on_operation=None,
    on_submitting=None,
    on_tick=None,
    *,
    api_key: str | None = None,
    require_byok: bool = False,
) -> dict:
    """Run refined Atlas splat reconstruction from panorama slices."""
    settings = get_settings()
    effective_key = _resolve_world_key(api_key, require_byok)
    if not effective_key:
        return await _mock_world(
            [settings.worldlabs_model],
            on_operation=on_operation,
            on_submitting=on_submitting,
            on_tick=on_tick,
        )
    byok_key = effective_key if (api_key and api_key.strip()) else None
    await _require_credits("atlas-refined", api_key=byok_key)
    timeout = httpx.Timeout(60.0, read=180.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await run_atlas_pipeline(
                client,
                api_key=effective_key,
                panorama_bytes=image_bytes,
                project_label=display_name,
                base_url=settings.atlas_api_base_url,
                slice_count=settings.atlas_slice_count,
                hfov_deg=settings.atlas_slice_hfov_deg,
                draft=False,
                synthesize_baseline=settings.atlas_synthesize_baseline,
                pose_timeout_s=DRAFT_WORLD_TIMEOUT_S,
                splat_timeout_s=FULL_WORLD_TIMEOUT_S,
                max_submit_attempts=getattr(settings, "atlas_max_submit_attempts_per_job", 2),
                on_operation=on_operation,
                on_submitting=on_submitting,
                on_tick=on_tick,
            )
    except AtlasError as exc:
        raise _map_atlas_exc(exc) from exc


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


def queue_unshared_world_deletes(world_ids: list[str | None], reason: str) -> int:
    ids = list(dict.fromkeys(valid_world_id(item) for item in world_ids if valid_world_id(item)))
    if not ids or not get_settings().worldlabs_api_key:
        return 0
    shared = world_ids_in_use(ids)
    queued = [item for item in ids if item not in shared]
    if queued:
        queue_world_deletes(queued, reason)
    return len(queued)


async def delete_worlds(world_ids: list[str | None]) -> None:
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
            else:
                queue_world_deletes([world_id], error or "delete failed")


_DRAFT_CLEANUP_ATTEMPTS = 24


async def retry_pending_world_deletes(*, force: bool = False) -> int:
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
            if world_ids_in_use([world_id]):
                forget_world_delete(world_id)
                continue
            ok, error = await _delete_one(client, headers, world_id)
            if ok:
                forget_world_delete(world_id)
                cleared += 1
            else:
                queue_world_deletes([world_id], error or "delete failed")
        for operation_id, attempts in cleanups:
            if attempts >= _DRAFT_CLEANUP_ATTEMPTS:
                forget_draft_cleanup(operation_id)
                continue
            checked = valid_world_id(operation_id)
            if checked is None:
                forget_draft_cleanup(operation_id)
                continue
            try:
                resp = await client.get(f"{settings.atlas_api_base_url.rstrip('/')}/operations/{checked}", headers=headers)
            except Exception:
                note_draft_cleanup_attempt(checked)
                continue
            if resp.status_code >= 400:
                note_draft_cleanup_attempt(checked)
                continue
            body = resp.json() if resp.content else {}
            if isinstance(body, dict) and body.get("done"):
                asset_id, _url = extract_splat_asset(body.get("response"))
                if asset_id:
                    queue_world_deletes([asset_id], "orphaned draft")
                forget_draft_cleanup(checked)
            else:
                note_draft_cleanup_attempt(checked)
    return cleared


async def _delete_one(client: httpx.AsyncClient, headers: dict, world_id: str) -> tuple[bool, str | None]:
    if valid_world_id(world_id) is None:
        return False, "invalid world id"
    base = get_settings().atlas_api_base_url.rstrip("/")
    url = f"{base}/assets/{world_id}"
    try:
        response = await client.delete(url, headers=headers)
    except Exception:
        logger.exception("Could not delete Atlas asset %s", world_id)
        return False, "request failed"
    if response.status_code in {200, 204, 404}:
        logger.info("Deleted Atlas asset %s", world_id)
        return True, None
    return False, f"HTTP {response.status_code}"
