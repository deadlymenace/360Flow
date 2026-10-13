"""Public, unlisted share pages. The token is the access control."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from db import get_by_share_token
from routers.responses import jpeg_response, labeled_jpeg
from storage import get_storage
from viewer import render_pano_page, render_world_page

router = APIRouter()


def _project(token: str):
    project = get_by_share_token(token)
    if project is None:
        raise HTTPException(status_code=404, detail="Shared project not found")
    return project


@router.get("/v/{token}", response_class=HTMLResponse)
async def view_panorama(token: str, view: str = Query(default="original"), embed: int = Query(default=0)):
    project = _project(token)
    if not project.panorama_key:
        raise HTTPException(status_code=404, detail="Panorama not ready yet")
    html = render_pano_page(
        title=project.name,
        asset_base=f"/v/{token}",
        has_staged=bool(project.staged_key),
        has_world=project.status == "complete",
        initial=view,
        embed=bool(embed),
    )
    return HTMLResponse(html)


@router.get("/v/{token}/world", response_class=HTMLResponse)
async def view_world(token: str):
    project = _project(token)
    if project.status != "complete" and not project.panorama_key:
        raise HTTPException(status_code=404, detail="World not ready yet")
    if not project.panorama_key:
        raise HTTPException(status_code=404, detail="Panorama not ready yet")
    html = render_world_page(
        title=project.name,
        asset_base=f"/v/{token}",
        marble_url=project.world_marble_url,
        has_staged=bool(project.staged_key),
    )
    return HTMLResponse(html)


@router.get("/v/{token}/panorama.jpg")
async def panorama_image(token: str, download: bool = Query(default=False)):
    project = _project(token)
    if not project.panorama_key:
        raise HTTPException(status_code=404, detail="Panorama not ready yet")
    data = get_storage().get_bytes(project.panorama_key)
    return jpeg_response(data, "original.jpg", download)


@router.get("/v/{token}/staged.jpg")
async def staged_image(token: str, download: bool = Query(default=False)):
    project = _project(token)
    if not project.staged_key:
        raise HTTPException(status_code=404, detail="Staged panorama not ready yet")
    data = get_storage().get_bytes(project.staged_key)
    return labeled_jpeg(data, "virtually-staged.jpg", download)


@router.get("/v/{token}/texture.jpg")
async def staged_texture(token: str):
    """Unlabeled staged JPEG used as the sphere texture.

    The flat export is ``staged.jpg``, which always carries the disclosure
    banner. Painting that banner into the sphere would smear it across the floor,
    so the viewer samples this clean texture and draws the label as a HUD instead.
    """
    project = _project(token)
    if not project.staged_key:
        raise HTTPException(status_code=404, detail="Staged panorama not ready yet")
    data = get_storage().get_bytes(project.staged_key)
    return jpeg_response(data, "staged-texture.jpg", download=False)
