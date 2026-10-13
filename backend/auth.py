"""Shared API key. When API_KEY is unset, /api/v1 stays open for local development."""

from __future__ import annotations

from fastapi import Header, HTTPException

from config import get_settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().api_key
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
