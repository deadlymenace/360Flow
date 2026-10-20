"""Public share pages and the owner's private preview.

The share token is the public access control. Staged images stay off that page
until the owner publishes them. The preview token is a separate unguessable
link used by the owner app so compare and export work before publish. It is
not printed on the public page. It expires on the same clock as the share
link, and rotating the share link rotates the preview token too.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from db import ProjectRecord, get_by_preview_token, get_by_share_token
from services.world_urls import valid_marble_url
from routers.responses import jpeg_response, labeled_jpeg
from storage import get_storage
from routers.projects import world_asset_response
from services.world_assets import world_asset_stream
from viewer import render_pano_page, render_splat_page, render_world_page

router = APIRouter()

# Invalid, expired, and rotated share tokens all use this body. A different
# sentence would tell a caller which of those happened.
_SHARE_MISSING = "Not found"


def _public_project(token: str) -> ProjectRecord:
    project = get_by_share_token(token)
    if project is None:
        raise HTTPException(status_code=404, detail=_SHARE_MISSING)
    return project


def _preview_project(token: str) -> ProjectRecord:
    project = get_by_preview_token(token)
    if project is None:
        raise HTTPException(status_code=404, detail="Preview not found")
    return project


def _show_staged(project: ProjectRecord, *, private: bool) -> bool:
    if not project.staged_key:
        return False
    if private:
        return True
    return bool(project.staged_published)


def _world_note(project: ProjectRecord, *, show_staged: bool) -> str:
    if project.status == "delayed":
        return "Your 3D tour is delayed, we'll finish it shortly. You can look around this panorama now."
    if show_staged and project.active_job == "staged_world":
        return "A 3D walkthrough of the staged room is generating."
    if project.world_preview_url and not project.world_marble_url:
        return "A fast 3D preview is ready. The full walkthrough is still generating."
    if project.active_job == "world" or (
        project.status == "generating_3d" and project.active_job != "staged_world"
    ):
        return "The 3D walkthrough is generating. You can look around this panorama now."
    return ""


def _panorama_page(project: ProjectRecord, asset_base: str, view: str, embed: int, *, private: bool) -> HTMLResponse:
    if not project.panorama_key:
        raise HTTPException(status_code=404, detail="Panorama not ready yet")
    show_staged = _show_staged(project, private=private)
    html = render_pano_page(
        title=project.name,
        asset_base=asset_base,
        has_staged=show_staged,
        has_world=True,
        initial=view,
        embed=bool(embed),
        world_note="" if embed else _world_note(project, show_staged=show_staged),
    )
    return HTMLResponse(html)


def _world_page(project: ProjectRecord, asset_base: str, source: str, *, private: bool) -> HTMLResponse:
    if source not in {"original", "staged"}:
        raise HTTPException(status_code=400, detail="source must be original or staged")
    if not project.panorama_key:
        raise HTTPException(status_code=404, detail="Panorama not ready yet")
    show_staged = _show_staged(project, private=private)
    staged_source = source == "staged"
    if staged_source and not show_staged:
        raise HTTPException(status_code=404, detail="Staged panorama not ready yet")
    # The Marble page is private to our API account and the CDN asset URLs are
    # unsigned capability links, so neither is ever put in this HTML.
    if staged_source:
        ready = bool(valid_marble_url(project.staged_world_marble_url) or valid_marble_url(project.staged_world_preview_url))
        preview = bool(ready and project.staged_world_preview_url and not project.staged_world_marble_url)
        generating = project.active_job == "staged_world"
    else:
        ready = bool(valid_marble_url(project.world_marble_url) or valid_marble_url(project.world_preview_url))
        preview = bool(ready and valid_marble_url(project.world_preview_url) and not project.world_marble_url)
        generating = project.active_job == "world" or (
            project.status == "generating_3d" and project.active_job != "staged_world"
        )
    full_world = bool(project.staged_world_marble_url if staged_source else project.world_marble_url)
    if full_world:
        # Self-hosted Spark viewer; the splat streams from our private cache.
        return HTMLResponse(
            render_splat_page(
                title=project.name,
                asset_base=asset_base,
                staged_source=staged_source,
                has_staged=show_staged and bool(project.staged_world_marble_url),
            )
        )
    waiting = ""
    if project.status == "delayed":
        waiting = "Your 3D tour is delayed, we'll finish it shortly. You can look around this panorama now."
    html = render_world_page(
        title=project.name,
        asset_base=asset_base,
        world_ready=ready,
        has_staged=show_staged,
        staged_source=staged_source,
        preview=preview,
        generating=generating,
        waiting=waiting,
    )
    return HTMLResponse(html)


def _original_jpeg(project: ProjectRecord, download: bool):
    if not project.panorama_key:
        raise HTTPException(status_code=404, detail="Panorama not ready yet")
    data = get_storage().get_bytes(project.panorama_key)
    return jpeg_response(data, "original.jpg", download)


def _staged_jpeg(project: ProjectRecord, download: bool, *, private: bool):
    key = project.staged_key
    if not key or not _show_staged(project, private=private):
        raise HTTPException(status_code=404, detail="Staged panorama not ready yet")
    data = get_storage().get_bytes(key)
    return labeled_jpeg(data, "virtually-staged.jpg", download)


def _texture_jpeg(project: ProjectRecord, *, private: bool):
    key = project.staged_key
    if not key or not _show_staged(project, private=private):
        raise HTTPException(status_code=404, detail="Staged panorama not ready yet")
    data = get_storage().get_bytes(key)
    return jpeg_response(data, "staged-texture.jpg", download=False)


@router.get("/v/{token}", response_class=HTMLResponse)
async def view_panorama(token: str, view: str = Query(default="original"), embed: int = Query(default=0)):
    project = _public_project(token)
    return _panorama_page(project, f"/v/{token}", view, embed, private=False)


@router.get("/p/{token}", response_class=HTMLResponse)
async def preview_panorama(token: str, view: str = Query(default="original"), embed: int = Query(default=0)):
    project = _preview_project(token)
    return _panorama_page(project, f"/p/{token}", view, embed, private=True)


@router.get("/v/{token}/world", response_class=HTMLResponse)
async def view_world(token: str, source: str = Query(default="original")):
    project = _public_project(token)
    return _world_page(project, f"/v/{token}", source, private=False)


@router.get("/p/{token}/world", response_class=HTMLResponse)
async def preview_world(token: str, source: str = Query(default="original")):
    project = _preview_project(token)
    return _world_page(project, f"/p/{token}", source, private=True)


@router.get("/v/{token}/panorama.jpg")
async def panorama_image(token: str, download: bool = Query(default=False)):
    return _original_jpeg(_public_project(token), download)


@router.get("/p/{token}/panorama.jpg")
async def preview_panorama_image(token: str, download: bool = Query(default=False)):
    return _original_jpeg(_preview_project(token), download)


@router.get("/v/{token}/staged.jpg")
async def staged_image(token: str, download: bool = Query(default=False)):
    return _staged_jpeg(_public_project(token), download, private=False)


@router.get("/p/{token}/staged.jpg")
async def preview_staged_image(token: str, download: bool = Query(default=False)):
    return _staged_jpeg(_preview_project(token), download, private=True)


@router.get("/v/{token}/texture.jpg")
async def staged_texture(token: str):
    """Unlabeled staged JPEG used as the sphere texture.

    The flat export is ``staged.jpg``, which always carries the disclosure
    banner. Painting that banner into the sphere would smear it across the floor,
    so the viewer samples this clean texture and draws the label as a HUD instead.
    Public callers only receive it after the owner publishes the staged view.
    """
    return _texture_jpeg(_public_project(token), private=False)


@router.get("/p/{token}/texture.jpg")
async def preview_staged_texture(token: str):
    return _texture_jpeg(_preview_project(token), private=True)


async def _world_splat(project: ProjectRecord, source: str, *, private: bool):
    if source not in {"original", "staged"}:
        raise HTTPException(status_code=400, detail="source must be original or staged")
    if source == "staged":
        if not _show_staged(project, private=private) or not project.staged_world_marble_url:
            raise HTTPException(status_code=404, detail="3D world not ready yet")
    elif not project.world_marble_url:
        raise HTTPException(status_code=404, detail="3D world not ready yet")
    found = await world_asset_stream(project, source, "spz")
    if found is None:
        raise HTTPException(status_code=404, detail="3D world file not available")
    return world_asset_response(found, "spz")


@router.get("/v/{token}/world.spz")
async def world_splat(token: str, source: str = Query(default="original")):
    """The share link's splat, served from our private cache (never the CDN URL)."""
    return await _world_splat(_public_project(token), source, private=False)


@router.get("/p/{token}/world.spz")
async def preview_world_splat(token: str, source: str = Query(default="original")):
    return await _world_splat(_preview_project(token), source, private=True)
