"""Storage keys and the local / R2 backend selector."""

from __future__ import annotations

from config import Settings, get_settings
from storage.local import LocalStorage
from storage.s3 import S3Storage

Storage = LocalStorage | S3Storage

_storage: Storage | None = None


def upload_key(project_id: str, index: int) -> str:
    return f"uploads/{project_id}/{index:02d}.jpg"


def panorama_key(project_id: str) -> str:
    return f"panoramas/{project_id}.jpg"


def staged_key(project_id: str) -> str:
    return f"staged/{project_id}.jpg"


def world_asset_prefix(project_id: str) -> str:
    return f"worlds/{project_id}/"


def project_asset_prefixes(project_id: str) -> list[str]:
    """Every object this API stores for a capture, on local disk or R2."""
    return [
        f"uploads/{project_id}/",
        panorama_key(project_id),
        staged_key(project_id),
        world_asset_prefix(project_id),
    ]


def delete_project_assets(project_id: str) -> None:
    storage = get_storage()
    for prefix in project_asset_prefixes(project_id):
        storage.delete_prefix(prefix)


def init_storage(settings: Settings | None = None) -> Storage:
    global _storage
    settings = settings or get_settings()
    if settings.uses_object_storage:
        _storage = S3Storage(
            endpoint_url=settings.s3_endpoint_url,
            bucket=settings.s3_bucket or "",
            access_key=settings.s3_access_key_id or "",
            secret_key=settings.s3_secret_access_key or "",
            region=settings.s3_region,
        )
    else:
        _storage = LocalStorage(settings.storage_dir)
    _storage.ensure_ready()
    return _storage


def get_storage() -> Storage:
    if _storage is None:
        return init_storage()
    return _storage
