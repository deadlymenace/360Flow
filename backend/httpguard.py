"""Request guards: closed-by-default CORS, rate limits, and share-page headers."""

from __future__ import annotations

import hashlib
import json

from config import get_settings
from ratelimit import limiter

_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (
        b"content-security-policy",
        (
            "default-src 'none'; "
            "img-src 'self' data:; "
            "style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; "
            "frame-src https://marble.worldlabs.ai https://*.worldlabs.ai; "
            "connect-src 'self'; "
            "base-uri 'none'; "
            "form-action 'none'; "
            "frame-ancestors 'none'"
        ).encode(),
    ),
]


def _header_map(scope) -> dict[str, str]:
    found = {}
    for key, value in scope.get("headers") or []:
        found[key.decode("latin1").lower()] = value.decode("latin1")
    return found


def _client_ip(scope, headers: dict[str, str]) -> str:
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    client = scope.get("client")
    if client and client[0]:
        return client[0]
    return "unknown"


def _bucket(headers: dict[str, str], ip: str) -> str:
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip().encode()
        return "device:" + hashlib.sha256(token).hexdigest()
    api_key = headers.get("x-api-key", "")
    if api_key:
        return "admin:" + hashlib.sha256(api_key.encode()).hexdigest()
    return "ip:" + ip


class GuardMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = _header_map(scope)
        path = scope.get("path") or ""
        method = scope.get("method") or "GET"
        origin = headers.get("origin")
        settings = get_settings()
        allowed = origin is not None and origin in settings.cors_origins
        ip = _client_ip(scope, headers)

        if path.startswith("/api/") and not self._within_limit(path, method, headers, ip, settings):
            await _send_json(send, 429, {"detail": "Too many requests"})
            return

        if method == "OPTIONS" and origin:
            if not allowed:
                await _send_json(send, 400, {"detail": "Origin is not allowed"})
                return
            await _send_empty(
                send,
                204,
                [
                    (b"access-control-allow-origin", origin.encode()),
                    (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
                    (b"access-control-allow-headers", b"Authorization, Content-Type, X-API-Key"),
                    (b"vary", b"Origin"),
                ],
            )
            return

        async def send_guarded(message):
            if message["type"] == "http.response.start":
                extra = []
                if allowed and origin:
                    extra.append((b"access-control-allow-origin", origin.encode()))
                    extra.append((b"vary", b"Origin"))
                if path.startswith("/v/"):
                    extra.extend(_SECURITY_HEADERS)
                if extra:
                    merged = list(message.get("headers") or [])
                    merged.extend(extra)
                    message = {**message, "headers": merged}
            await send(message)

        await self.app(scope, receive, send_guarded)

    def _within_limit(self, path: str, method: str, headers: dict[str, str], ip: str, settings) -> bool:
        if path == "/api/v1/devices" and method == "POST":
            if not limiter.allow(f"register:{ip}", settings.device_registrations_per_hour, 3600):
                return False
        return limiter.allow(_bucket(headers, ip), settings.rate_limit_per_minute, 60)


async def _send_json(send, status: int, body: dict) -> None:
    raw = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})


async def _send_empty(send, status: int, headers: list[tuple[bytes, bytes]]) -> None:
    merged = list(headers) + [(b"content-length", b"0")]
    await send({"type": "http.response.start", "status": status, "headers": merged})
    await send({"type": "http.response.body", "body": b""})
