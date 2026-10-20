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
    "delayed": 70,
    "complete": 100,
    "failed": 100,
}


def share_url(token: str, request: Request | None = None) -> str | None:
    """Absolute share link from PUBLIC_BASE_URL only.

    The Host header is not used, so a caller cannot choose the link origin.
    """
    del request
    base = get_settings().public_base_url
    if not base or not token:
        return None
    return f"{base.rstrip('/')}/v/{token}"


def preview_url(token: str | None) -> str | None:
    """Owner-only viewer. It is not the public share link."""
    if not token:
        return None
    base = get_settings().public_base_url
    if not base:
        return None
    return f"{base.rstrip('/')}/p/{token}"


def to_project(row: ProjectRecord, request: Request | None = None, *, admin: bool = False) -> Project:
    share = share_url(row.share_token, request)
    private = preview_url(row.preview_token)
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
        share_expires_at=row.share_expires_at,
        preview_url=private,
        has_original=bool(row.panorama_key),
        has_staged=staged,
        staged_published=bool(row.staged_published),
        staging_provider=row.staging_provider,
        world_provider=row.world_provider,
        world_viewer_url=f"{share}/world" if row.panorama_key else None,
        world_ready=bool(row.world_marble_url),
        world_draft_ready=bool(row.world_preview_url) and not row.world_marble_url,
        staged_world_ready=bool(row.staged_world_marble_url),
        staged_world_draft_ready=bool(row.staged_world_preview_url) and not row.staged_world_marble_url,
        staged_world_viewer_url=f"{private}/world?source=staged" if staged and private else None,
        staging_notes=row.staging_notes,
        staging_status=row.staging_status,
        staging_error=row.staging_error,
        active_job=row.active_job,
        disclosure=DISCLOSURE_TEXT if staged else None,
        provider_error=row.provider_error if admin else None,
    )


def status_body(row: ProjectRecord, request: Request | None = None) -> dict:
    return {
        "status": row.status,
        "progress": _PROGRESS.get(row.status, 0),
        "error": row.error,
        "share_url": share_url(row.share_token),
        "has_original": bool(row.panorama_key),
        "has_staged": bool(row.staged_key),
        "staged_published": bool(row.staged_published),
        "staging_provider": row.staging_provider,
        "staging_status": row.staging_status,
        "staging_error": row.staging_error,
        "world_provider": row.world_provider,
        "world_ready": bool(row.world_marble_url),
        "world_draft_ready": bool(row.world_preview_url) and not row.world_marble_url,
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
