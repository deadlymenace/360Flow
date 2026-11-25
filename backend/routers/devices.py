"""Anonymous device registration and "Delete all my data". The raw token is returned once."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from auth import Caller, _admin_key_matches, require_caller
from config import get_settings
from db import cancel_device_jobs, create_device, delete_device, delete_project, list_projects
from services.byok import clear_project_keys
from services.pipeline import abort_local_jobs
from services.world_gen import queue_unshared_world_deletes
from storage import delete_project_assets

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/devices")
async def register_device(x_api_key: str | None = Header(default=None)):
    settings = get_settings()
    if not settings.allow_open_device_registration and not _admin_key_matches(x_api_key):
        raise HTTPException(status_code=403, detail="Open device registration is disabled")
    device_id, token = create_device()
    return {"device_id": device_id, "token": token}


@router.delete("/devices/me", status_code=204)
async def delete_all_my_data(caller: Caller = Depends(require_caller)) -> Response:
    """Delete all my data (App Store guideline 5.1.1(v))."""
    if caller.device_id is None:
        raise HTTPException(status_code=400, detail="Admin key has no device to delete")
    cancelled = cancel_device_jobs(caller.device_id)
    abort_local_jobs(cancelled)
    projects = list_projects(caller.device_id)
    world_ids: list[str | None] = []
    for project in projects:
        clear_project_keys(project.id)
        world_ids.extend(
            [project.world_id, project.world_preview_id, project.staged_world_id, project.staged_world_preview_id]
        )
        delete_project_assets(project.id)
        delete_project(project.id)
    if not delete_device(caller.device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        queue_unshared_world_deletes(world_ids, "queued by Delete all my data")
    except Exception:
        logger.exception("Could not queue World Labs deletes for device %s; data was still deleted", caller.device_id)
    logger.info(
        "Deleted device %s, %d projects, cancelled %d jobs", caller.device_id, len(projects), len(cancelled)
    )
    return Response(status_code=204)
