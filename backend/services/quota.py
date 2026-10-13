"""Daily limits for paid Gemini and World Labs calls, per device."""

from __future__ import annotations

from config import get_settings
from db import GLOBAL_QUOTA_DEVICE, consume_daily_quota, daily_quota_available

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


def world_quota_blocks(device_id: str | None) -> bool:
    settings = get_settings()
    if not settings.worldlabs_api_key:
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
    return False


def consume_world_quota(device_id: str | None) -> bool:
    """Stand-alone charge. Job enqueue charges inside its own transaction instead."""
    from db import _EnqueueStop, charge_world_quota, session_scope

    try:
        with session_scope() as session:
            if not charge_world_quota(session, device_id):
                raise _EnqueueStop("quota")
        return True
    except _EnqueueStop:
        return False
