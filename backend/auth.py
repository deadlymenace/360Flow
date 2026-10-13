"""Per-device tokens, plus an optional admin key that stays on the server.

The mobile app registers with POST /api/v1/devices and sends the token as
Authorization: Bearer. The admin key (API_KEY) is for operators only and is
compared with hmac.compare_digest. It is never embedded in the app.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException

from config import get_settings
from db import device_id_for_token


@dataclass(frozen=True)
class Caller:
    device_id: str | None
    is_admin: bool


def _admin_key_matches(presented: str | None) -> bool:
    expected = get_settings().api_key
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


def require_caller(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Caller:
    if _admin_key_matches(x_api_key):
        return Caller(device_id=None, is_admin=True)
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing device token")
    device_id = device_id_for_token(token)
    if device_id is None:
        raise HTTPException(status_code=401, detail="Invalid device token")
    return Caller(device_id=device_id, is_admin=False)
