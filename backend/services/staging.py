"""Optional virtual staging through Gemini, with a mock when the key is unset.

The panorama is split into overlapping perspective views. Each view is edited
with the same preservation prompt, checked against the original edges, and
blended back. Ceiling and floor stay the unstaged photograph.
"""

from __future__ import annotations

import base64
import logging

import cv2
import httpx
import numpy as np

from config import get_settings
from services.disclosure import build_staging_prompt, decode_image, encode_jpeg, fit_equirect
from services.equirect_views import (
    PerspectiveView,
    reproject_views,
    split_views,
    structure_score,
)
from services.staging_mock import apply_mock_grade, mock_stage

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1/models/{model}:generateContent"
STRUCTURE_MIN = 0.45


def build_gemini_request(prompt: str, jpeg_bytes: bytes, image_size: str) -> dict:
    """REST body for Nano Banana image editing on the Gemini API.

    Documented at https://ai.google.dev/gemini-api/docs/image-generation
    (generateContent, responseModalities TEXT+IMAGE, inline image bytes).
    """
    return {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64.b64encode(jpeg_bytes).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": "4:3",
                "imageSize": image_size,
            },
        },
    }


def extract_gemini_image(body: dict) -> bytes:
    candidates = body.get("candidates") or []
    texts: list[str] = []
    for candidate in candidates:
        parts = ((candidate.get("content") or {}).get("parts")) or []
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
            if part.get("text"):
                texts.append(str(part["text"]))
    detail = " ".join(texts).strip()
    raise RuntimeError(detail or "Gemini did not return an image")


async def stage_image(image_bytes: bytes, user_prompt: str) -> tuple[bytes, str, str | None]:
    """Return a clean staged JPEG, the provider id, and any structure notes.

    The disclosure banner is applied later, when the image is served.
    """
    settings = get_settings()
    equirect = fit_equirect(decode_image(image_bytes))
    views = split_views(equirect)
    prompt = build_staging_prompt(user_prompt, perspective=True)
    provider = "gemini" if settings.gemini_api_key else "gemini_mock"
    if provider == "gemini_mock":
        await mock_stage("memory", user_prompt)

    edited: list[PerspectiveView] = []
    notes: list[str] = []
    for view in views:
        image, note = await _edit_view(view, prompt, settings)
        edited.append(PerspectiveView(view.yaw_deg, view.hfov_deg, image))
        if note:
            notes.append(note)
    staged = reproject_views(equirect, edited)
    joined = " ".join(notes) if notes else None
    return encode_jpeg(staged), provider, joined


async def _edit_view(
    view: PerspectiveView,
    prompt: str,
    settings,
) -> tuple[np.ndarray, str | None]:
    original = view.image

    async def produce(text: str) -> np.ndarray:
        if not settings.gemini_api_key:
            return apply_mock_grade(original)
        raw = await _gemini_edit(settings.gemini_api_key, settings.gemini_model, text, original, settings.gemini_image_size)
        image = decode_image(raw)
        if image.shape[:2] != original.shape[:2]:
            image = cv2.resize(image, (original.shape[1], original.shape[0]), interpolation=cv2.INTER_AREA)
        return image

    edited = await produce(prompt)
    if structure_score(original, edited) >= STRUCTURE_MIN:
        return edited, None
    logger.info("Structure check failed at yaw %.0f°, retrying", view.yaw_deg)
    retried = await produce(
        prompt + "\nThe previous edit changed the architecture. Restore walls, windows, floors, and fixtures exactly."
    )
    if structure_score(original, retried) >= STRUCTURE_MIN:
        return retried, None
    return (
        original,
        f"Kept the original photo at {view.yaw_deg:.0f}° because the edit changed walls or windows.",
    )


async def _gemini_edit(api_key: str, model: str, prompt: str, image_bgr: np.ndarray, image_size: str) -> bytes:
    payload = build_gemini_request(prompt, encode_jpeg(image_bgr, quality=90), image_size)
    timeout = httpx.Timeout(30.0, read=120.0)
    url = GEMINI_URL.format(model=model)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
    if response.status_code >= 400:
        message = _gemini_error(response)
        raise RuntimeError(f"Gemini rejected the staging request ({response.status_code}): {message}")
    return extract_gemini_image(response.json())


def _gemini_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        return "request failed"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:240]
    return "request failed"
