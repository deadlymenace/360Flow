"""Request guards: closed-by-default CORS, rate limits, and share-page headers."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from fastapi import HTTPException

from auth import _admin_key_matches
from config import get_settings
from db import device_id_for_token
from ratelimit import limiter

_UPLOAD_OVERHEAD = 256 * 1024


def _header_map(scope) -> dict[str, str]:
    found = {}
    for key, value in scope.get("headers") or []:
        found[key.decode("latin1").lower()] = value.decode("latin1")
    return found


def client_ip(socket_host: str | None, forwarded_for: str | None, trusted_proxy_count: int) -> str:
    """Client address from the trusted proxy chain.

    ``trusted_proxy_count`` is how many proxies append to X-Forwarded-For
    (Railway's edge is 1). The client is that many hops in from the right.
    A shorter header is ignored so a caller cannot supply the address.
    """
    direct = (socket_host or "").strip() or "unknown"
    if trusted_proxy_count <= 0 or not forwarded_for:
        return direct
    hops = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    if len(hops) < trusted_proxy_count:
        return direct
    return hops[-trusted_proxy_count]


def upload_byte_cap(max_images: int, max_image_bytes: int) -> int:
    return max_images * max_image_bytes + _UPLOAD_OVERHEAD


def capping_receive(receive, limit: int):
    """Stop reading the body once ``limit`` bytes have arrived.

    The chunk that would pass the cap is not forwarded, and later chunks are
    not read.
    """
    total = 0

    async def inner():
        nonlocal total
        message = await receive()
        if message["type"] == "http.request":
            body = message.get("body") or b""
            if total + len(body) > limit:
                raise HTTPException(status_code=413, detail="Upload is too large")
            total += len(body)
        return message

    return inner


def share_security_headers(query_string: bytes, ancestors: list[str]) -> list[tuple[bytes, bytes]]:
    """Headers for public share pages.

    ``embed=1`` replaces ``frame-ancestors 'none'`` with the configured
    allowlist so the in-app WebView and listing embeds are not blocked.
    """
    embed = _embed_requested(query_string)
    if embed:
        sources = " ".join(ancestors) if ancestors else "*"
        frame = f"frame-ancestors {sources}"
    else:
        frame = "frame-ancestors 'none'"
    policy = (
        "default-src 'none'; "
        "img-src 'self' data:; "
        "style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; "
        "frame-src https://marble.worldlabs.ai https://*.worldlabs.ai; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        f"{frame}"
    )
    return [
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-robots-tag", b"noindex"),
        (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
        (b"content-security-policy", policy.encode()),
    ]


def _embed_requested(query_string: bytes) -> bool:
    params = parse_qs(query_string.decode("latin1"))
    value = (params.get("embed") or [""])[0].strip().lower()
    return value in {"1", "true"}


def _socket_host(scope) -> str | None:
    client = scope.get("client")
    if client and client[0]:
        return client[0]
    return None


def _content_length(headers: dict[str, str]) -> int | None:
    raw = headers.get("content-length", "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _bucket(headers: dict[str, str], ip: str) -> str:
    """Verified callers get their own bucket. Failed auth shares the client IP.

    A rotating bearer token must not mint a fresh limit. Only a token that
    matches a device, or a matching admin key, leaves the IP bucket.
    """
    if _admin_key_matches(headers.get("x-api-key")):
        return "admin"
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token:
            device_id = device_id_for_token(token)
            if device_id:
                return "device:" + device_id
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
        ip = client_ip(_socket_host(scope), headers.get("x-forwarded-for"), settings.trusted_proxy_count)

        if method == "POST" and path.endswith("/upload"):
            cap = upload_byte_cap(settings.max_images, settings.max_image_bytes)
            declared = _content_length(headers)
            if declared is not None and declared > cap:
                await _send_json(send, 413, {"detail": "Upload is too large"})
                return
            receive = capping_receive(receive, cap)

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
                    extra.extend(
                        share_security_headers(scope.get("query_string") or b"", settings.embed_frame_ancestors)
                    )
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
