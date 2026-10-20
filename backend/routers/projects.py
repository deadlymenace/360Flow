"""Project, upload, stitch, staging, and world-generation endpoints."""

from __future__ import annotations

import math
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from auth import Caller, require_caller
from db import TRANSIENT_STATUSES, cancel_delayed_project, delete_project, get_project, rotate_share_token, update_project
from db import create_project as create_project_row
from db import list_projects as list_project_rows
from models.project import CreateProjectRequest, Project, StageRequest, UploadMetadata
from routers.responses import jpeg_response, labeled_jpeg, status_body, to_project
from services.disclosure import DISCLOSURE_TEXT
from services.quota import STAGING_QUOTA_MESSAGE, WORLD_QUOTA_MESSAGE, staging_quota_blocks, world_quota_blocks
from services.pipeline import enqueue_job, retry_delayed_project, start_job
from services.uploads import normalize_upload
from services.world_gen import queue_unshared_world_deletes
from storage import delete_project_assets, get_storage, panorama_key, staged_key, upload_key
from config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


def _bind_caller(request: Request, caller: Caller = Depends(require_caller)) -> None:
    request.state.caller = caller


router.dependencies.append(Depends(_bind_caller))


async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    declared = getattr(upload, "size", None)
    if declared is not None and declared > limit:
        raise HTTPException(
            status_code=413,
            detail=f"Each image must be at most {limit // (1024 * 1024)} MB",
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        if total + len(chunk) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Each image must be at most {limit // (1024 * 1024)} MB",
            )
        total += len(chunk)
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="Empty image upload")
    return b"".join(chunks)


def _parse_metadata(raw: str | None, count: int) -> UploadMetadata:
    if not raw:
        return UploadMetadata()
    try:
        meta = UploadMetadata.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="metadata must be JSON") from exc
    if meta.hfov_deg is not None and not 30 <= meta.hfov_deg <= 150:
        raise HTTPException(status_code=400, detail="hfov_deg must be between 30 and 150")
    if meta.yaws_deg is not None and len(meta.yaws_deg) != count:
        raise HTTPException(status_code=400, detail="yaws_deg must have one value per image")
    if meta.pitches_deg is not None and len(meta.pitches_deg) != count:
        raise HTTPException(status_code=400, detail="pitches_deg must have one value per image")
    # Omitted pitch (build 7 clients) is fine: it means "not sent". A sent
    # pitch must be a real number within ±90°; NaN/inf is rejected, not coerced.
    if meta.pitches_deg is not None and any(not math.isfinite(p) or abs(p) > 90 for p in meta.pitches_deg):
        raise HTTPException(status_code=400, detail="pitches_deg must be finite and within ±90")
    return meta


def _caller(request: Request) -> Caller:
    caller = getattr(request.state, "caller", None)
    if caller is None:
        raise HTTPException(status_code=401, detail="Missing device token")
    return caller


def _require(project_id: str, request: Request):
    caller = _caller(request)
    project = get_project(project_id)
    if project is None or (not caller.is_admin and project.owner_device_id != caller.device_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _reject_declared_body(request: Request) -> None:
    raw = request.headers.get("content-length")
    if not raw:
        return
    try:
        length = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid upload") from exc
    settings = get_settings()
    overhead = 256 * 1024
    if length > settings.max_images * settings.max_image_bytes + overhead:
        raise HTTPException(status_code=413, detail="Upload is too large")


def _reject_if_busy(status: str) -> None:
    # Delayed 3D keeps the panorama usable: the owner can re-upload, cancel, or retry.
    busy = tuple(item for item in TRANSIENT_STATUSES if item != "delayed")
    if status in busy and not get_settings().jobs_inline:
        raise HTTPException(status_code=409, detail="This project is already processing")


def _raise_for_enqueue(outcome: str, kind: str) -> None:
    if outcome == "busy":
        raise HTTPException(status_code=409, detail="This project is already processing")
    if outcome == "missing":
        raise HTTPException(status_code=404, detail="Project not found")
    if outcome == "quota":
        message = WORLD_QUOTA_MESSAGE if kind in {"world", "staged_world"} else STAGING_QUOTA_MESSAGE
        raise HTTPException(status_code=429, detail=message)


@router.post("/projects")
async def create_project(req: CreateProjectRequest, request: Request) -> Project:
    caller = _caller(request)
    return to_project(create_project_row(req.name, owner_device_id=caller.device_id))


@router.get("/projects")
async def list_projects(request: Request) -> list[Project]:
    caller = _caller(request)
    rows = list_project_rows(owner_device_id=caller.device_id, admin=caller.is_admin)
    return [to_project(row, admin=caller.is_admin) for row in rows]


@router.get("/projects/{project_id}")
async def get_one(project_id: str, request: Request) -> Project:
    caller = _caller(request)
    return to_project(_require(project_id, request), admin=caller.is_admin)


@router.delete("/projects/{project_id}", status_code=204)
async def remove_project(project_id: str, request: Request) -> Response:
    """Delete the capture, its files, hosted worlds, and its share link."""
    project = _require(project_id, request)
    world_ids = [
        project.world_id,
        project.world_preview_id,
        project.staged_world_id,
        project.staged_world_preview_id,
    ]
    delete_project_assets(project_id)
    if not delete_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    # After the row is gone: keep any world another project still uses.
    # World Labs is called by the background retry pass, not in this request.
    queue_unshared_world_deletes(world_ids, "queued by project delete")
    return Response(status_code=204)


@router.post("/projects/{project_id}/share/rotate")
async def rotate_share(project_id: str, request: Request) -> Project:
    _require(project_id, request)
    updated = rotate_share_token(project_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return to_project(updated)


@router.post("/projects/{project_id}/upload")
async def upload_images(
    project_id: str,
    request: Request,
    images: list[UploadFile] = File(...),
    metadata: str | None = Form(default=None),
):
    project = _require(project_id, request)
    _reject_if_busy(project.status)
    _reject_declared_body(request)
    settings = get_settings()
    count = len(images)
    if count < settings.min_images or count > settings.max_images:
        raise HTTPException(
            status_code=400,
            detail=f"Upload between {settings.min_images} and {settings.max_images} images",
        )
    meta = _parse_metadata(metadata, count)
    payloads = [
        normalize_upload(await _read_limited(image, settings.max_image_bytes), max_pixels=settings.max_image_pixels)
        for image in images
    ]

    outcome, job_id = await enqueue_job(
        project_id,
        "stitch",
        project_fields={
            "image_count": count,
            "error": None,
            "provider_error": None,
            "hfov_deg": meta.hfov_deg,
            "yaws_json": json.dumps(meta.yaws_deg) if meta.yaws_deg is not None else None,
            "pitches_json": json.dumps(meta.pitches_deg) if meta.pitches_deg is not None else None,
            "lens": meta.lens,
            "panorama_key": None,
            "staged_key": None,
            "staging_prompt": None,
            "staging_provider": None,
            "staging_notes": None,
            "staging_status": None,
            "staging_error": None,
            "staged_published": 0,
            # New photos: an operation paid for on the old ones must not resume.
            "resume_operation": None,
        },
        replace_delayed=True,
        retire_worlds="all",
        start=False,
    )
    _raise_for_enqueue(outcome, "stitch")
    storage = get_storage()
    storage.delete_prefix(f"uploads/{project_id}/")
    storage.delete_prefix(panorama_key(project_id))
    storage.delete_prefix(staged_key(project_id))
    for index, payload in enumerate(payloads):
        storage.put_bytes(upload_key(project_id, index), payload, "image/jpeg")
    logger.info("Project %s: stored %d images", project_id, count)
    if job_id:
        await start_job(job_id)
    latest = get_project(project_id)
    return {"status": latest.status if latest else "stitching", "image_count": count}


@router.get("/projects/{project_id}/status")
async def get_status(project_id: str, request: Request):
    return status_body(_require(project_id, request))


@router.post("/projects/{project_id}/retry")
async def retry_project(project_id: str, request: Request):
    project = _require(project_id, request)
    if project.status == "delayed":
        outcome = await retry_delayed_project(project_id)
        if outcome == "expired":
            latest = get_project(project_id)
            return {"status": latest.status if latest else "failed", "error": latest.error if latest else None}
        if outcome != "queued":
            raise HTTPException(status_code=409, detail="This project is already processing")
        latest = get_project(project_id)
        return {"status": latest.status if latest else "generating_3d"}
    if project.status != "failed":
        raise HTTPException(status_code=400, detail="Only a failed or delayed project can be retried")
    storage = get_storage()
    uploads = storage.list_keys(f"uploads/{project_id}/")
    job = project.active_job
    if job == "stage":
        kind = "stage"
    elif job == "staged_world":
        kind = "staged_world"
    elif job == "world":
        kind = "world"
    elif uploads:
        kind = "stitch"
    else:
        raise HTTPException(status_code=400, detail="No uploaded images to retry")
    outcome, _job_id = await enqueue_job(project_id, kind)
    _raise_for_enqueue(outcome, kind)
    latest = get_project(project_id)
    return {"status": latest.status if latest else kind}


@router.post("/projects/{project_id}/cancel")
async def cancel_project(project_id: str, request: Request):
    """Stop a delayed 3D tour and leave the panorama in place."""
    _require(project_id, request)
    status = cancel_delayed_project(project_id)
    if status is None:
        raise HTTPException(status_code=400, detail="Only a delayed 3D tour can be cancelled")
    return {"status": status}


@router.get("/projects/{project_id}/panorama")
async def get_panorama(project_id: str, request: Request, download: bool = Query(default=False)):
    project = _require(project_id, request)
    if not project.panorama_key:
        raise HTTPException(status_code=404, detail="Panorama not ready yet")
    data = get_storage().get_bytes(project.panorama_key)
    return jpeg_response(data, f"{project_id}-original.jpg", download)


@router.get("/projects/{project_id}/staged")
async def get_staged(project_id: str, request: Request, download: bool = Query(default=False)):
    project = _require(project_id, request)
    if not project.staged_key:
        raise HTTPException(status_code=404, detail="Staged panorama not ready yet")
    data = get_storage().get_bytes(project.staged_key)
    return labeled_jpeg(data, f"{project_id}-staged.jpg", download)


@router.post("/projects/{project_id}/staged/publish")
async def publish_staged(project_id: str, request: Request) -> Project:
    """Let the public share page show the staged panorama and staged 3D."""
    project = _require(project_id, request)
    if not project.staged_key:
        raise HTTPException(status_code=400, detail="No staged image to publish")
    updated = update_project(project_id, staged_published=1)
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return to_project(updated)


@router.post("/projects/{project_id}/staged/unpublish")
async def unpublish_staged(project_id: str, request: Request) -> Project:
    """Hide the staged view from the public share page again."""
    _require(project_id, request)
    updated = update_project(project_id, staged_published=0)
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return to_project(updated)


@router.post("/projects/{project_id}/stage")
async def stage_project(project_id: str, req: StageRequest, request: Request):
    project = _require(project_id, request)
    if not project.panorama_key:
        raise HTTPException(status_code=400, detail="No panorama available to stage")
    if staging_quota_blocks(project.owner_device_id):
        raise HTTPException(status_code=429, detail=STAGING_QUOTA_MESSAGE)
    _reject_if_busy(project.status)
    if project.staging_status == "staging" and not get_settings().jobs_inline:
        raise HTTPException(status_code=409, detail="This project is already processing")
    outcome, _job_id = await enqueue_job(
        project_id,
        "stage",
        project_fields={"staging_prompt": req.prompt, "staged_published": 0},
    )
    _raise_for_enqueue(outcome, "stage")
    latest = get_project(project_id)
    return {"status": (latest.staging_status if latest else None) or "staging", "prompt": req.prompt}


@router.get("/projects/{project_id}/stage/status")
async def get_stage_status(project_id: str, request: Request):
    project = _require(project_id, request)
    provider = project.staging_provider
    if provider is None:
        provider = "gemini" if get_settings().gemini_api_key else "gemini_mock"
    if project.staging_status:
        stage_state = project.staging_status
    elif project.staged_key:
        stage_state = "staged"
    else:
        stage_state = "idle"
    return {
        "status": stage_state,
        "prompt": project.staging_prompt,
        "provider": provider,
        "has_staged": bool(project.staged_key),
        "disclosure": DISCLOSURE_TEXT if project.staged_key else None,
        "notes": project.staging_notes,
        "error": project.staging_error,
    }


@router.post("/projects/{project_id}/generate-3d")
async def generate_3d(project_id: str, request: Request, source: str = Query(default="original")):
    if source not in {"original", "staged"}:
        raise HTTPException(status_code=400, detail="source must be original or staged")
    project = _require(project_id, request)
    if source == "original" and not project.panorama_key:
        raise HTTPException(status_code=400, detail="No panorama available for a 3D world")
    if source == "staged" and not project.staged_key:
        raise HTTPException(status_code=400, detail="Stage the panorama before generating a 3D world from it")
    if world_quota_blocks(project.owner_device_id):
        raise HTTPException(status_code=429, detail=WORLD_QUOTA_MESSAGE)
    _reject_if_busy(project.status)
    kind = "staged_world" if source == "staged" else "world"
    outcome, _job_id = await enqueue_job(project_id, kind, retire_worlds="staged" if source == "staged" else "original")
    _raise_for_enqueue(outcome, kind)
    latest = get_project(project_id)
    return {"status": latest.status if latest else "generating_3d"}


@router.get("/projects/{project_id}/generate-3d/status")
async def get_generate_3d_status(project_id: str, request: Request):
    project = _require(project_id, request)
    provider = project.world_provider
    if provider is None:
        provider = "worldlabs" if get_settings().worldlabs_api_key else "worldlabs_mock"
    body = to_project(project)
    share = body.share_url or ""
    private = body.preview_url or share
    assets = None
    if project.panorama_key and (project.world_provider or project.world_marble_url or project.world_preview_url):
        assets = {
            "viewer_url": f"{share}/world",
            "marble_url": project.world_marble_url,
            "preview_url": project.world_preview_url,
            "splat_url": project.world_splat_url,
            "mesh_url": project.world_mesh_url,
            "thumbnail_url": project.world_thumbnail_url,
        }
    staged_assets = None
    if project.staged_key and (project.staged_world_provider or project.staged_world_marble_url):
        staged_assets = {
            "viewer_url": f"{private}/world?source=staged",
            "marble_url": project.staged_world_marble_url,
            "preview_url": project.staged_world_preview_url,
            "splat_url": project.staged_world_splat_url,
            "mesh_url": project.staged_world_mesh_url,
            "thumbnail_url": project.staged_world_thumbnail_url,
        }
    return {
        "status": project.status,
        "assets": assets,
        "staged_assets": staged_assets,
        "provider": provider,
        "error": project.error,
        "disclosure": DISCLOSURE_TEXT if project.staged_key else None,
    }
