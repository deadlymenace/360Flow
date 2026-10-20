"""Private cache of World Labs world assets (Gaussian splat + collider mesh).

World Labs CDN asset URLs are unsigned and do not expire, so they are treated
as secrets: the backend downloads them once into our own storage
(``worlds/{project_id}/``, private: never a public bucket or presigned link)
and serves them only through the device-auth API and the share/preview routes.
They are deleted with the project, by "Delete all my data", and on re-upload.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from config import get_settings
from storage import get_storage, world_asset_prefix

logger = logging.getLogger(__name__)

# Only World Labs hosts are ever fetched (no SSRF through a stored URL).
_ALLOWED_HOST_SUFFIX = ".worldlabs.ai"
MAX_SPLAT_BYTES = 300 * 1024 * 1024
MAX_MESH_BYTES = 100 * 1024 * 1024
DOWNLOAD_CHUNK = 1024 * 1024

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


async def _download(url: str, limit: int, dest: Path) -> bool:
    """Stream ``url`` to ``dest`` on disk with a size cap (never held in memory)."""
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
                return False
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                logger.warning("World asset too large (%s bytes)", declared)
                return False
            total = 0
            with dest.open("wb") as handle:
                async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK):
                    total += len(chunk)
                    if total > limit:
                        logger.warning("World asset exceeded %d bytes", limit)
                        return False
                    handle.write(chunk)
            return total > 0


_fill_locks: dict[tuple[int, str], asyncio.Lock] = {}


def fill_lock(project_id: str) -> asyncio.Lock:
    """Per-project lock so concurrent viewers trigger one cache fill, not N downloads.

    Keyed by event loop too (an asyncio.Lock is bound to one loop). Per process:
    across replicas the fill is idempotent (same key, same bytes).
    """
    key = (id(asyncio.get_running_loop()), project_id)
    lock = _fill_locks.get(key)
    if lock is None:
        lock = _fill_locks[key] = asyncio.Lock()
    return lock


async def _store_download(project_id: str, key: str, kind: str, url: str) -> bool:
    """Download to a temp file, then copy into storage in chunks."""
    content_type, limit = KINDS[kind]
    with tempfile.TemporaryDirectory(prefix="world-asset-") as tmp:
        path = Path(tmp) / f"asset.{kind}"
        try:
            ok = await _download(url, limit, path)
        except httpx.HTTPError as exc:
            logger.warning("World asset download for %s %s failed: %s", project_id, key, type(exc).__name__)
            return False
        if not ok or not path.is_file() or path.stat().st_size == 0:
            return False
        from db import get_project

        if get_project(project_id) is None:
            # Deleted while downloading: write nothing.
            return False
        storage = get_storage()
        await asyncio.to_thread(storage.put_file, key, path, content_type)
        if get_project(project_id) is None:
            storage.delete_prefix(world_asset_prefix(project_id))
            return False
        logger.info("Cached world %s for %s (%d bytes)", key.rsplit("/", 1)[-1], project_id, path.stat().st_size)
        return True


async def cache_world_asset(project_id: str, source: str, kind: str, url: object) -> bool:
    """Download one asset into private storage. Never logs the URL."""
    safe = allowed_asset_url(url)
    if safe is None:
        return False
    key = world_asset_key(project_id, source, kind)
    storage = get_storage()
    if storage.exists(key):
        return True
    async with fill_lock(project_id):
        if storage.exists(key):  # another request filled it while we waited
            return True
        return await _store_download(project_id, key, kind, safe)


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


async def world_asset_stream(project, source: str, kind: str):
    """(chunk iterator, size) of the cached asset; fills the cache lazily.

    Worlds finished before the cache existed are downloaded on first view
    (one download per project at a time, see ``fill_lock``).
    """
    key = world_asset_key(project.id, source, kind)
    storage = get_storage()
    if not storage.exists(key):
        staged = source == "staged"
        attr = {"spz": "splat_url", "glb": "mesh_url"}[kind]
        url = getattr(project, f"{'staged_' if staged else ''}world_{attr}", None)
        if not await cache_world_asset(project.id, source, kind, url):
            return None
    try:
        return await asyncio.to_thread(storage.open_stream, key)
    except FileNotFoundError:
        return None


async def world_asset_bytes(project, source: str, kind: str) -> bytes | None:
    """Whole file in memory. Tests/tools only; routes use ``world_asset_stream``."""
    found = await world_asset_stream(project, source, kind)
    if found is None:
        return None
    chunks, _size = found
    return b"".join(chunks)


async def _fetch_world_record(world_id: str) -> dict | None:
    """Read-only GET of an existing World Labs world (no generation, no credits)."""
    from services.world_gen import WORLDLABS_BASE
    from services.world_urls import valid_world_id

    settings = get_settings()
    if not settings.worldlabs_api_key or valid_world_id(world_id) is None:
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False) as client:
        response = await client.get(
            f"{WORLDLABS_BASE}/marble/v1/worlds/{world_id}",
            headers={"WLT-Api-Key": settings.worldlabs_api_key},
        )
    if response.status_code != 200:
        logger.warning("World Labs world lookup for %s returned HTTP %d", world_id, response.status_code)
        return None
    body = response.json()
    if not isinstance(body, dict):
        return None
    world = body.get("world") if isinstance(body.get("world"), dict) else body
    return world


async def refetch_lowest_splat(project, source: str = "original", *, apply: bool = False) -> str:
    """Re-cache a world's splat at its lowest resolution (one-off maintenance).

    Looks the existing world up (GET only: never generates a world or spends
    credits), picks the smallest ``spz_urls`` entry, downloads it, then replaces
    ``worlds/{id}/{source}.spz`` and the stored URL. ``apply=False`` only
    reports what would change. Returns a short status (never a URL).
    """
    from db import update_project
    from services.world_gen import parse_world

    staged = source == "staged"
    world_id = project.staged_world_id if staged else project.world_id
    if not world_id:
        return "skip: no world"
    world = await _fetch_world_record(world_id)
    if world is None:
        return "skip: world lookup failed"
    smallest = allowed_asset_url(parse_world({"response": world}).get("splat_url"))
    if smallest is None:
        return "skip: no splat url"
    current = project.staged_world_splat_url if staged else project.world_splat_url
    key = world_asset_key(project.id, source, "spz")
    if smallest == current and get_storage().exists(key):
        return "ok: already lowest-res"
    if not apply:
        return "would refetch"
    async with fill_lock(project.id):
        # Clear the old cached file, then download the low-res one.
        get_storage().delete_prefix(key)
        if not await _store_download(project.id, key, "spz", smallest):
            return "error: download failed (cache cleared; next view re-fills from the stored URL)"
    field = "staged_world_splat_url" if staged else "world_splat_url"
    update_project(project.id, **{field: smallest})
    return "refetched"
