"""Normalize phone uploads to oriented JPEGs."""

from __future__ import annotations

import io

from fastapi import HTTPException
from PIL import Image, ImageOps


def normalize_upload(data: bytes, *, max_pixels: int = 24_000_000) -> bytes:
    if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")):
        raise HTTPException(status_code=400, detail="Images must be JPEG or PNG")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            if image.width < 32 or image.height < 32:
                raise HTTPException(status_code=400, detail="Image is too small")
            if image.width * image.height > max_pixels:
                raise HTTPException(status_code=400, detail="Image dimensions are too large")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=92)
            return output.getvalue()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not decode image") from exc
