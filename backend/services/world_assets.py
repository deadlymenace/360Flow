"""Private cache of World Labs world assets (Gaussian splat + collider mesh).

World Labs CDN asset URLs are unsigned and do not expire, so they are treated
as secrets: the backend downloads them once into our own storage
(``worlds/{project_id}/``, private: never a public bucket or presigned link)
and serves them only through the device-auth API and the share/preview routes.
They are deleted with the project, by "Delete all my data", and on re-upload.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from config import get_settings
from storage import get_storage, world_asset_prefix

logger = logging.getLogger(__name__)

# Only World Labs hosts are ever fetched (no SSRF through a stored URL).
_ALLOWED_HOST_SUFFIX = ".worldlabs.ai"
MAX_SPLAT_BYTES = 300 * 1024 * 1024
MAX_MESH_BYTES = 100 * 1024 * 1024

KINDS = {
    "spz": ("application/octet-stream", MAX_SPLAT_BYTES),
    "glb": ("model/gltf-binary", MAX_MESH_BYTES),
}


def world_asset_key(project_id: str, source: str, kind: str) -> str:
    if source not in {"original", "staged"} or kind not in KINDS:
        raise ValueError("bad world asset")
    return f"{world_asset_prefix(project_id)}{source}.{kind}"


def allowed_asset_url(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(_ALLOWED_HOST_SUFFIX):
        return None
    if host.startswith("mock."):
        return None
    return url


async def _download(url: str, limit: int) -> bytes | None:
    settings = get_settings()
    headers = {}
    if settings.worldlabs_api_key:
        # Sent to World Labs hosts only (checked by allowed_asset_url).
        headers["WLT-Api-Key"] = settings.worldlabs_api_key
    timeout = httpx.Timeout(30.0, read=300.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                logger.warning("World asset download failed: HTTP %d", response.status_code)
                return None
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                logger.warning("World asset too large (%s bytes)", declared)
                return None
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > limit:
                    logger.warning("World asset exceeded %d bytes", limit)
                    return None
                chunks.append(chunk)
            return b"".join(chunks)


async def cache_world_asset(project_id: str, source: str, kind: str, url: object) -> bool:
    """Download one asset into private storage. Never logs the URL."""
    safe = allowed_asset_url(url)
    if safe is None:
        return False
    key = world_asset_key(project_id, source, kind)
    storage = get_storage()
    if storage.exists(key):
        return True
    content_type, limit = KINDS[kind]
    try:
        data = await _download(safe, limit)
    except httpx.HTTPError as exc:
        logger.warning("World asset download for %s %s/%s failed: %s", project_id, source, kind, type(exc).__name__)
        return False
    if not data:
        return False
    from db import get_project

    if get_project(project_id) is None:
        # Deleted while downloading: write nothing.
        return False
    storage.put_bytes(key, data, content_type)
    if get_project(project_id) is None:
        storage.delete_prefix(world_asset_prefix(project_id))
        return False
    logger.info("Cached world %s for %s (%s, %d bytes)", kind, project_id, source, len(data))
    return True


async def cache_world_assets(project_id: str, source: str, splat_url: object, mesh_url: object) -> None:
    for kind, url in (("spz", splat_url), ("glb", mesh_url)):
        try:
            await cache_world_asset(project_id, source, kind, url)
        except Exception:
            logger.exception("Caching world %s for %s failed", kind, project_id)


def clear_world_assets(project_id: str, source: str | None = None) -> None:
    storage = get_storage()
    if source is None:
        storage.delete_prefix(world_asset_prefix(project_id))
        return
    for kind in KINDS:
        storage.delete_prefix(world_asset_key(project_id, source, kind))


async def world_asset_bytes(project, source: str, kind: str) -> bytes | None:
    """Cached bytes; fetches lazily for worlds finished before the cache existed."""
    key = world_asset_key(project.id, source, kind)
    storage = get_storage()
    if not storage.exists(key):
        staged = source == "staged"
        attr = {"spz": "splat_url", "glb": "mesh_url"}[kind]
        url = getattr(project, f"{'staged_' if staged else ''}world_{attr}", None)
        if not await cache_world_asset(project.id, source, kind, url):
            return None
    return storage.get_bytes(key)
