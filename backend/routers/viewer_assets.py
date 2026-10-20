"""Vendored, open-source viewer libraries (three.js r180 (+ postprocessing/Pass.js, which Spark imports), Spark 2.3.1).

Served from our origin so share pages need no third-party script hosts.
Only these files are reachable; nothing user-specific lives here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

_VENDOR = Path(__file__).resolve().parent.parent / "static" / "vendor"
_FILES = {
    "three.module.min.js",
    "three.core.min.js",
    "spark-2.3.1.module.min.js",
    "three-addons-Pass.js",
}


@router.get("/viewer-assets/{name}")
async def viewer_asset(name: str):
    if name not in _FILES:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        _VENDOR / name,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )
