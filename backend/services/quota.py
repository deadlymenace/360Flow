"""Daily and monthly limits for paid Gemini and World Labs calls, per device/buyer."""

from __future__ import annotations

from config import get_settings
from db import (
    GLOBAL_QUOTA_DEVICE,
    _effective_monthly_world_limit,
    consume_daily_quota,
    daily_quota_available,
    monthly_quota_available,
    session_scope,
)

STAGING_QUOTA_MESSAGE = "Daily AI staging limit reached. Try again tomorrow."
WORLD_QUOTA_MESSAGE = "Daily 3D limit reached. Try again tomorrow."


def staging_quota_blocks(device_id: str | None) -> bool:
    settings = get_settings()
    if not settings.gemini_api_key or settings.gemini_daily_limit <= 0:
        return False
    return not daily_quota_available(device_id, "gemini", settings.gemini_daily_limit)


def consume_staging_quota(device_id: str | None) -> bool:
    settings = get_settings()
    if not settings.gemini_api_key or settings.gemini_daily_limit <= 0:
        return True
    return consume_daily_quota(device_id, "gemini", settings.gemini_daily_limit)


def reserved_world_credits() -> int:
    """Credits held when a world is queued: the full model, plus the draft when it is on."""
    from services.world_gen import credits_required

    settings = get_settings()
    total = credits_required(settings.worldlabs_model)
    if settings.worldlabs_draft_model:
        total += credits_required(settings.worldlabs_draft_model)
    return total


def world_quota_blocks(device_id: str | None, *, byok: bool = False) -> bool:
    settings = get_settings()
    if device_id:
        with session_scope() as session:
            monthly_limit = _effective_monthly_world_limit(session, device_id)
        if monthly_limit > 0 and (byok or settings.worldlabs_api_key):
            if not monthly_quota_available(device_id, "worldlabs", monthly_limit):
                return True
    if byok or not settings.worldlabs_api_key:
        return False
    if (
        device_id
        and settings.worldlabs_daily_limit > 0
        and not daily_quota_available(device_id, "worldlabs", settings.worldlabs_daily_limit)
    ):
        return True
    if settings.global_daily_worlds_cap > 0 and not daily_quota_available(
        GLOBAL_QUOTA_DEVICE, "worldlabs", settings.global_daily_worlds_cap
    ):
        return True
    credits = reserved_world_credits()
    if settings.global_daily_credits_cap > 0 and not daily_quota_available(
        GLOBAL_QUOTA_DEVICE,
        "wl_credits",
        settings.global_daily_credits_cap,
        needed=credits,
    ):
        return True
    if settings.global_monthly_credits_cap > 0 and not monthly_quota_available(
        GLOBAL_QUOTA_DEVICE,
        "wl_credits",
        settings.global_monthly_credits_cap,
        needed=credits,
    ):
        return True
    return False


def consume_world_quota(device_id: str | None, *, byok: bool = False) -> bool:
    """Stand-alone charge. Job enqueue charges inside its own transaction instead."""
    from db import _EnqueueStop, charge_world_quota

    try:
        with session_scope() as session:
            if not charge_world_quota(session, device_id, byok=byok):
                raise _EnqueueStop("quota")
        return True
    except _EnqueueStop:
        return False
