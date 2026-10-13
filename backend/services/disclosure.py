"""Staging disclosure and the preservation prompt sent to Gemini.

The label is burned into every staged JPEG the API serves, and the viewers
draw the same words on screen. The original panorama is never passed through
this function. Preservation is best effort: the model can still change the room.
"""

from __future__ import annotations

import cv2
import numpy as np

DISCLOSURE_TEXT = "Virtually staged (AI)"

# BGR. Chosen so tests can recognize the bar and so it stays readable on photos.
_BAR_COLOR = (46, 26, 26)
_TEXT_COLOR = (170, 236, 255)


def build_staging_prompt(user_prompt: str, *, perspective: bool = False) -> str:
    """Fixed preservation instructions, then the user's style and furniture only.

    Gemini cannot guarantee it will obey this. Callers must treat the result as
    best effort and keep the unstaged original. Perspective views share one
    furniture list so the five crops of a panorama stay consistent.
    """
    style = " ".join(user_prompt.split())
    if perspective:
        opening = (
            "Edit this perspective photograph. It is one view of a real room that was "
            "captured as a 360° panorama. Other views of the same room use this exact furniture list."
        )
        closing = (
            "Keep the added furniture consistent with that list. Do not change the camera angle. "
            "Output one photorealistic photograph of this same view. No people, no text, no watermarks."
        )
    else:
        opening = "Edit this equirectangular 360° photograph of a real room."
        closing = (
            "Output one photorealistic 2:1 equirectangular image. No people, no text, no watermarks, no extra rooms."
        )
    return (
        f"{opening}\n\n"
        "PRESERVE EXACTLY. Do not change, move, remove, recolor, reshape, or replace any of the following: "
        "walls, wall color, trim, and built-in cabinetry; windows, doors, and glass; "
        "floors, ceiling, and flooring material; installed fixtures including lights, outlets, vents, "
        "radiators, plumbing, and built-in appliances; room geometry; camera viewpoint. "
        "Do not change the view through the windows, the time of day, or the direction of the light.\n\n"
        "ONLY ADD movable furniture and decor that is not part of the building: seating, tables, rugs, "
        "art, plants, lamps, cushions, and similar loose items. Do not remove architecture to make room. "
        "If the style request below asks to renovate, paint, or replace walls, windows, floors, or fixtures, "
        "ignore that part and only add furniture and decor.\n\n"
        f"Style and furniture to add: {style}\n\n"
        f"{closing}"
    )


def apply_disclosure_label(image_bgr: np.ndarray) -> np.ndarray:
    """Burn a full-width banner into a flat export. Returns a new image."""
    labeled = image_bgr.copy()
    height, width = labeled.shape[:2]
    bar_h = max(32, int(round(height * 0.046)))
    y0 = height - bar_h
    region = labeled[y0:height].astype(np.float32)
    color = np.array(_BAR_COLOR, dtype=np.float32)
    labeled[y0:height] = np.clip(region * 0.12 + color * 0.88, 0, 255).astype(np.uint8)

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.55, min(2.2, width / 1400.0))
    thickness = max(1, int(round(scale * 2)))
    (text_w, text_h), _ = cv2.getTextSize(DISCLOSURE_TEXT, font, scale, thickness)
    x = max(8, (width - text_w) // 2)
    y = min(height - 8, y0 + (bar_h + text_h) // 2)
    cv2.putText(
        labeled,
        DISCLOSURE_TEXT,
        (x, y),
        font,
        scale,
        (20, 20, 20),
        thickness + 2,
        cv2.LINE_AA,
    )
    cv2.putText(
        labeled,
        DISCLOSURE_TEXT,
        (x, y),
        font,
        scale,
        _TEXT_COLOR,
        thickness,
        cv2.LINE_AA,
    )
    return labeled


def fit_equirect(image_bgr: np.ndarray) -> np.ndarray:
    """Letterbox an image onto a 2:1 canvas without stretching the photo."""
    height, width = image_bgr.shape[:2]
    if width == height * 2:
        return image_bgr
    target_w = max(width, height * 2)
    target_w -= target_w % 2
    target_w = max(target_w, 2)
    target_h = target_w // 2
    cover = cv2.resize(image_bgr, (target_w, target_h), interpolation=cv2.INTER_AREA)
    cover = cv2.GaussianBlur(cover, (0, 0), 24)
    scale = min(target_w / width, target_h / height)
    fitted_w = max(1, int(round(width * scale)))
    fitted_h = max(1, int(round(height * scale)))
    fitted = cv2.resize(image_bgr, (fitted_w, fitted_h), interpolation=cv2.INTER_AREA)
    x = (target_w - fitted_w) // 2
    y = (target_h - fitted_h) // 2
    cover[y : y + fitted_h, x : x + fitted_w] = fitted
    return cover


def decode_image(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not decode image")
    return image


def encode_jpeg(image_bgr: np.ndarray, quality: int = 95) -> bytes:
    ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ValueError("Could not encode JPEG")
    return buf.tobytes()
