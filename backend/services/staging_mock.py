"""Preview stand-in used when GEMINI_API_KEY is not set.

The mock does not add furniture. It applies a warm grade so before/after is
visible and waits the configured delay. The caller reports provider
``gemini_mock``. The original file is left untouched by the caller.
"""

from __future__ import annotations

import asyncio

import cv2
import numpy as np

from config import get_settings


async def mock_stage(panorama_path: str, prompt: str) -> dict:
    await asyncio.sleep(get_settings().mock_stage_delay)
    return {
        "status": "staged",
        "staged_image_path": panorama_path,
        "prompt_used": prompt,
        "provider": "gemini_mock",
    }


def apply_mock_grade(image_bgr: np.ndarray) -> np.ndarray:
    """Warm the panorama so the unstaged original is easy to tell apart."""
    graded = image_bgr.astype(np.float32)
    graded[..., 2] = np.clip(graded[..., 2] * 1.18 + 14, 0, 255)
    graded[..., 1] = np.clip(graded[..., 1] * 1.04, 0, 255)
    graded[..., 0] = np.clip(graded[..., 0] * 0.9, 0, 255)
    graded = graded.astype(np.uint8)
    height, width = graded.shape[:2]
    # A soft vignette-free warmth is enough; keep geometry identical.
    return cv2.convertScaleAbs(graded, alpha=1.02, beta=2)
