"""Anonymous device registration. The raw token is returned once."""

from __future__ import annotations

from fastapi import APIRouter

from db import create_device

router = APIRouter()


@router.post("/devices")
async def register_device():
    device_id, token = create_device()
    return {"device_id": device_id, "token": token}
