"""Vendored, open-source viewer libraries (three.js r180 (+ postprocessing/Pass.js, which Spark imports), Spark 2.3.1).

Served from our origin so share pages need no third-party script hosts.
Only these files are reachable; nothing user-specific lives here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

_STATIC = Path(__file__).resolve().parent.parent / "static"
_VENDOR = _STATIC / "vendor"
_FILES = {
    "three.module.min.js",
    "three.core.min.js",
    "spark-2.3.1.module.min.js",
    "three-addons-Pass.js",
}
# Our own page scripts (moved out of inline <script> so the CSP needs no
# 'unsafe-inline'). Pages link them with ?v=<content hash>.
_APP_FILES = {"pano-viewer.js", "splat-viewer.js"}


@router.get("/viewer-assets/{name}")
async def viewer_asset(name: str):
    if name in _FILES:
        path, cache = _VENDOR / name, "public, max-age=31536000, immutable"
    elif name in _APP_FILES:
        path, cache = _STATIC / "viewer" / name, "public, max-age=300"
    else:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        path,
        media_type="text/javascript",
        headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"},
    )
