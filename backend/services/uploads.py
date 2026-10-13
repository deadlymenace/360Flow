"""Normalize phone uploads to oriented JPEGs.

Dimensions are read from the JPEG or PNG header before the pixels are decoded.
Pillow's decompression-bomb guard is set to the same pixel cap.
"""

from __future__ import annotations

import io
import struct

from fastapi import HTTPException
from PIL import Image, ImageOps

MAX_IMAGE_PIXELS = 24_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}
_EXIF_IFD = 0x8769
_FOCAL_LENGTH = 0x920A
_FOCAL_35MM = 0xA405


def header_dimensions(data: bytes) -> tuple[int, int]:
    """Width and height from the image header, without decoding pixels."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise HTTPException(status_code=400, detail="Could not read image")
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(data)
    raise HTTPException(status_code=400, detail="Images must be JPEG or PNG")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    index = 2
    length = len(data)
    while index + 3 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        while index < length and data[index] == 0xFF:
            index += 1
        if index >= length:
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > length:
            break
        segment = struct.unpack(">H", data[index : index + 2])[0]
        if segment < 2 or index + segment > length:
            break
        if marker in _SOF_MARKERS and segment >= 7:
            height, width = struct.unpack(">HH", data[index + 3 : index + 7])
            return int(width), int(height)
        index += segment
    raise HTTPException(status_code=400, detail="Could not read image")


def _reject_dimensions(width: int, height: int, max_pixels: int) -> None:
    if width < 32 or height < 32:
        raise HTTPException(status_code=400, detail="Image is too small")
    if width > 100_000 or height > 100_000 or width * height > max_pixels:
        raise HTTPException(status_code=400, detail="Image dimensions are too large")


def _exif_ifd(image: Image.Image) -> dict:
    """Phone photos store focal length in the Exif IFD, not in IFD0."""
    exif = image.getexif()
    if not exif:
        return {}
    try:
        return exif.get_ifd(_EXIF_IFD)
    except Exception:
        return {}


def read_focal_tags(image: Image.Image) -> tuple[object | None, int | None]:
    """Return ``(FocalLength, FocalLengthIn35mmFilm)`` from the Exif IFD."""
    ifd = _exif_ifd(image)
    focal = ifd.get(_FOCAL_LENGTH) if ifd else None
    raw_35 = ifd.get(_FOCAL_35MM) if ifd else None
    return focal, _positive_int(raw_35)


def focal_35mm_from_image(image: Image.Image) -> int | None:
    return read_focal_tags(image)[1]


def _positive_int(raw: object) -> int | None:
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def normalize_upload(data: bytes, *, max_pixels: int = MAX_IMAGE_PIXELS) -> bytes:
    width, height = header_dimensions(data)
    _reject_dimensions(width, height, max_pixels)
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with Image.open(io.BytesIO(data)) as image:
            focal, focal_35 = read_focal_tags(image)
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            _reject_dimensions(image.width, image.height, max_pixels)
            output = io.BytesIO()
            save_kwargs: dict = {"format": "JPEG", "quality": 92}
            if focal_35 or focal:
                preserved = Image.Exif()
                ifd = preserved.get_ifd(_EXIF_IFD)
                if focal_35:
                    ifd[_FOCAL_35MM] = focal_35
                if focal:
                    ifd[_FOCAL_LENGTH] = focal
                save_kwargs["exif"] = preserved
            image.save(output, **save_kwargs)
            return output.getvalue()
    except HTTPException:
        raise
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=400, detail="Image dimensions are too large") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not decode image") from exc
