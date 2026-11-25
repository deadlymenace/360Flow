"""Operator-only management for revocable, subscription-linked buyer API keys."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import Caller, require_admin
from db import create_buyer_key, list_buyer_keys, revoke_buyer_key, update_buyer_key

router = APIRouter(dependencies=[Depends(require_admin)])


class CreateBuyerKeyRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    subscription_expires_at: Optional[datetime] = None
    monthly_world_limit: Optional[int] = Field(default=None, ge=0, le=10000)


class UpdateBuyerKeyRequest(BaseModel):
    active: Optional[bool] = None
    subscription_expires_at: Optional[datetime] = None
    clear_expiry: bool = False
    monthly_world_limit: Optional[int] = Field(default=None, ge=0, le=10000)


@router.post("/admin/keys")
async def issue_buyer_key(req: CreateBuyerKeyRequest, _admin: Caller = Depends(require_admin)):
    meta, raw_key = create_buyer_key(
        req.label,
        subscription_expires_at=req.subscription_expires_at,
        monthly_world_limit=req.monthly_world_limit,
    )
    return {**meta, "api_key": raw_key}


@router.get("/admin/keys")
async def get_buyer_keys(_admin: Caller = Depends(require_admin)):
    return {"keys": list_buyer_keys()}


@router.post("/admin/keys/{key_id}/revoke")
async def revoke_key(key_id: str, _admin: Caller = Depends(require_admin)):
    updated = revoke_buyer_key(key_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Buyer key not found")
    return updated


@router.patch("/admin/keys/{key_id}")
async def patch_key(key_id: str, req: UpdateBuyerKeyRequest, _admin: Caller = Depends(require_admin)):
    kwargs: dict[str, object] = {}
    if req.active is not None:
        kwargs["active"] = req.active
    if req.clear_expiry:
        kwargs["subscription_expires_at"] = None
    elif req.subscription_expires_at is not None:
        kwargs["subscription_expires_at"] = req.subscription_expires_at
    if req.monthly_world_limit is not None:
        kwargs["monthly_world_limit"] = req.monthly_world_limit
    updated = update_buyer_key(key_id, **kwargs)
    if updated is None:
        raise HTTPException(status_code=404, detail="Buyer key not found")
    return updated
