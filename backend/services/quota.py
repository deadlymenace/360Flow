"""Daily limits for paid Gemini and World Labs calls, per device."""

from __future__ import annotations

from config import get_settings
from db import consume_daily_quota, daily_quota_available

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
    if not settings.worldlabs_api_key or settings.worldlabs_daily_limit <= 0:
        return False
    return not daily_quota_available(device_id, "worldlabs", settings.worldlabs_daily_limit)


def consume_world_quota(device_id: str | None) -> bool:
    settings = get_settings()
    if not settings.worldlabs_api_key or settings.worldlabs_daily_limit <= 0:
        return True
    return consume_daily_quota(device_id, "worldlabs", settings.worldlabs_daily_limit)
