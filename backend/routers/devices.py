"""Anonymous device registration and "Delete all my data". The raw token is returned once."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response

from auth import Caller, require_caller
from db import create_device, delete_device, delete_project, list_projects
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

    Deletes every project owned by the calling device: uploaded photos,
    panoramas and staged images in storage, the project rows with their jobs,
    and so their share and preview links. Then the device token is revoked, so
    it returns 401 from then on.
    """
    if caller.device_id is None:
        raise HTTPException(status_code=400, detail="Admin key has no device to delete")
    projects = list_projects(caller.device_id)
    for project in projects:
        # TODO: also delete hosted World Labs worlds (world_id, world_preview_id,
        # staged_world_id, staged_world_preview_id) via services.world_gen.delete_worlds
        # once that is approved for this route. They are not deleted yet.
        delete_project_assets(project.id)
        delete_project(project.id)
    if not delete_device(caller.device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    logger.info("Deleted device %s and %d projects", caller.device_id, len(projects))
    return Response(status_code=204)
