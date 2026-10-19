"""Anonymous device registration. The raw token is returned once."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from auth import Caller, require_caller

from db import create_device, delete_device

router = APIRouter()


@router.post("/devices")
async def register_device():
    device_id, token = create_device()
    return {"device_id": device_id, "token": token}


@router.delete("/devices/me", status_code=204)
async def delete_my_device(caller: Caller = Depends(require_caller)):
    """Revoke the calling device's token. Projects it owns are not deleted here."""
    if caller.device_id is None:
        raise HTTPException(status_code=400, detail="Admin key has no device to delete")
    if not delete_device(caller.device_id):
        raise HTTPException(status_code=404, detail="Device not found")
