"""Per-device tokens, revocable buyer keys, plus an optional admin key.

The mobile app registers with POST /api/v1/devices and sends the token as
Authorization: Bearer. Issued buyer keys (prefix ``f360_``) work via
Authorization: Bearer or X-Buyer-Key, and stop working immediately when revoked
or when their subscription_expires_at passes.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException

from config import get_settings
from db import authenticate_buyer_key, device_id_for_token


@dataclass(frozen=True)
class Caller:
    device_id: str | None
    is_admin: bool
    key_id: str | None = None
    monthly_world_limit: int | None = None


def _admin_key_matches(presented: str | None) -> bool:
    expected = get_settings().api_key
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


def _extract_token(authorization: str | None, x_buyer_key: str | None) -> str:
    if x_buyer_key and x_buyer_key.strip():
        return x_buyer_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def require_caller(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_buyer_key: str | None = Header(default=None),
) -> Caller:
    if _admin_key_matches(x_api_key):
        return Caller(device_id=None, is_admin=True)
    token = _extract_token(authorization, x_buyer_key)
    if not token:
        raise HTTPException(status_code=401, detail="Missing API key or device token")
    if token.startswith("f360_"):
        status, key_id, device_id, monthly_limit = authenticate_buyer_key(token)
        if status == "ok" and device_id:
            return Caller(device_id=device_id, is_admin=False, key_id=key_id, monthly_world_limit=monthly_limit)
        if status == "inactive":
            raise HTTPException(status_code=401, detail="API key is inactive or subscription expired")
        raise HTTPException(status_code=401, detail="Invalid API key")
    device_id = device_id_for_token(token)
    if device_id is None:
        raise HTTPException(status_code=401, detail="Invalid device token")
    return Caller(device_id=device_id, is_admin=False)


def require_admin(x_api_key: str | None = Header(default=None)) -> Caller:
    if not get_settings().api_key:
        raise HTTPException(status_code=403, detail="Operator API_KEY is not configured")
    if not _admin_key_matches(x_api_key):
        raise HTTPException(status_code=401, detail="Admin access required")
    return Caller(device_id=None, is_admin=True)
