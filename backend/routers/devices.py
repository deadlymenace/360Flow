"""Anonymous device registration and "Delete all my data". The raw token is returned once."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response

from auth import Caller, require_caller
from db import cancel_device_jobs, create_device, delete_device, delete_project, list_projects
from services.pipeline import abort_local_jobs
from services.world_gen import delete_worlds
from storage import delete_project_assets

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/devices")
async def register_device():
    device_id, token = create_device()
    return {"device_id": device_id, "token": token}


@router.delete("/devices/me", status_code=204)
async def delete_all_my_data(caller: Caller = Depends(require_caller)) -> Response:
    """Delete all my data (App Store guideline 5.1.1(v)).

    First cancels the device's queued and running jobs and drops their leases,
    so a paid provider call that returns later writes nothing. Then deletes every project owned by the calling device: uploaded photos,
    panoramas and staged images in storage, the project rows with their jobs,
    and so their share and preview links. Then the device token is revoked, so
    it returns 401 from then on. Last, World Labs is asked to delete the
    hosted worlds, best-effort: a failure is logged (and queued for retry by
    delete_worlds) and never blocks the data deletion.
    """
    if caller.device_id is None:
        raise HTTPException(status_code=400, detail="Admin key has no device to delete")
    cancelled = cancel_device_jobs(caller.device_id)
    abort_local_jobs(cancelled)
    projects = list_projects(caller.device_id)
    world_ids: list[str | None] = []
    for project in projects:
        world_ids.extend(
            [project.world_id, project.world_preview_id, project.staged_world_id, project.staged_world_preview_id]
        )
        delete_project_assets(project.id)
        delete_project(project.id)
    if not delete_device(caller.device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        await delete_worlds(world_ids)
    except Exception:
        logger.exception("World Labs delete failed for device %s; data was still deleted", caller.device_id)
    logger.info(
        "Deleted device %s, %d projects, cancelled %d jobs", caller.device_id, len(projects), len(cancelled)
    )
    return Response(status_code=204)
