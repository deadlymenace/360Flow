"""Shared JSON and JPEG responses."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response

from config import get_settings
from db import ProjectRecord
from models.project import Project, ProjectStatus
from services.disclosure import DISCLOSURE_TEXT, apply_disclosure_label, decode_image, encode_jpeg

_PROGRESS = {
    "created": 0,
    "uploading": 10,
    "stitching": 40,
    "stitched": 100,
    "staging": 55,
    "staged": 100,
    "generating_3d": 70,
    "complete": 100,
    "failed": 100,
}


def share_url(token: str, request: Request | None = None) -> str | None:
    """Absolute share link from PUBLIC_BASE_URL only.

    The Host header is not used, so a caller cannot choose the link origin.
    """
    del request
    base = get_settings().public_base_url
    if not base:
        return None
    return f"{base.rstrip('/')}/v/{token}"


def to_project(row: ProjectRecord, request: Request | None = None) -> Project:
    share = share_url(row.share_token, request)
    staged = bool(row.staged_key)
    return Project(
        id=row.id,
        name=row.name,
        status=ProjectStatus(row.status),
        created_at=row.created_at,
        image_count=row.image_count,
        panorama_path=row.panorama_key,
        error=row.error,
        staging_prompt=row.staging_prompt,
        share_token=row.share_token,
        share_url=share,
        has_original=bool(row.panorama_key),
        has_staged=staged,
        staging_provider=row.staging_provider,
        world_provider=row.world_provider,
        world_viewer_url=f"{share}/world" if row.panorama_key else None,
        world_marble_url=row.world_marble_url,
        world_thumbnail_url=row.world_thumbnail_url,
        world_splat_url=row.world_splat_url,
        world_mesh_url=row.world_mesh_url,
        world_preview_url=row.world_preview_url,
        staged_world_marble_url=row.staged_world_marble_url,
        staged_world_preview_url=row.staged_world_preview_url,
        staged_world_viewer_url=f"{share}/world?source=staged" if staged else None,
        staging_notes=row.staging_notes,
        active_job=row.active_job,
        disclosure=DISCLOSURE_TEXT if staged else None,
    )


def status_body(row: ProjectRecord, request: Request | None = None) -> dict:
    return {
        "status": row.status,
        "progress": _PROGRESS.get(row.status, 0),
        "error": row.error,
        "share_url": share_url(row.share_token),
        "has_original": bool(row.panorama_key),
        "has_staged": bool(row.staged_key),
        "staging_provider": row.staging_provider,
        "world_provider": row.world_provider,
        "world_preview_url": row.world_preview_url,
        "active_job": row.active_job,
        "disclosure": DISCLOSURE_TEXT if row.staged_key else None,
    }


def jpeg_response(data: bytes, filename: str, download: bool = False) -> Response:
    disposition = "attachment" if download else "inline"
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "private, max-age=60",
        },
    )


def labeled_jpeg(data: bytes, filename: str, download: bool = False) -> Response:
    labeled = apply_disclosure_label(decode_image(data))
    return jpeg_response(encode_jpeg(labeled), filename, download)
