"""Virtual staging. Calls NanoBanana when NANOBANANA_API_KEY is set, else the mock."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from config import get_settings
from services.disclosure import build_staging_prompt, decode_image, encode_jpeg, fit_equirect
from services.staging_mock import apply_mock_grade, mock_stage

logger = logging.getLogger(__name__)

NANOBANANA_GENERATE = "https://api.nanobananaapi.ai/api/v1/nanobanana/generate-2"
NANOBANANA_STATUS = "https://api.nanobananaapi.ai/api/v1/nanobanana/record-info"


async def stage_image(image_bytes: bytes, user_prompt: str, image_url: str | None) -> tuple[bytes, str]:
    """Return clean staged JPEG bytes (no disclosure banner) and the provider id.

    The banner is applied when the image is served or downloaded. ``image_url``
    must be reachable by NanoBanana; the mock does not use it.
    """
    settings = get_settings()
    if not settings.nanobanana_api_key:
        await mock_stage("memory", user_prompt)
        graded = apply_mock_grade(decode_image(image_bytes))
        return encode_jpeg(fit_equirect(graded)), "nanobanana_mock"

    if not image_url:
        raise RuntimeError(
            "NanoBanana needs a public image URL. Set PUBLIC_BASE_URL to the Railway "
            "URL, or configure R2 so a presigned URL can be issued."
        )

    prompt = build_staging_prompt(user_prompt)
    raw = await _nanobanana_edit(settings.nanobanana_api_key, image_url, prompt, settings.nanobanana_resolution)
    staged = fit_equirect(decode_image(raw))
    return encode_jpeg(staged), "nanobanana"


async def _nanobanana_edit(api_key: str, image_url: str, prompt: str, resolution: str) -> bytes:
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "prompt": prompt,
        "imageUrls": [image_url],
        "aspectRatio": "auto",
        "resolution": resolution,
        "outputFormat": "jpg",
    }
    timeout = httpx.Timeout(60.0, read=120.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        created = await client.post(NANOBANANA_GENERATE, headers={**headers, "Content-Type": "application/json"}, json=payload)
        if created.status_code >= 400:
            raise RuntimeError(f"NanoBanana rejected the staging request ({created.status_code})")
        body = created.json()
        if body.get("code") not in (None, 200):
            raise RuntimeError(body.get("message") or body.get("msg") or "NanoBanana could not start staging")
        task_id = (body.get("data") or {}).get("taskId")
        if not task_id:
            raise RuntimeError("NanoBanana did not return a task id")

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            info = await client.get(NANOBANANA_STATUS, headers=headers, params={"taskId": task_id})
            if info.status_code >= 400:
                raise RuntimeError(f"NanoBanana status check failed ({info.status_code})")
            data = (info.json() or {}).get("data") or {}
            flag = data.get("successFlag")
            if flag == 1:
                result_url = (data.get("response") or {}).get("resultImageUrl")
                if not result_url:
                    raise RuntimeError("NanoBanana finished without an image URL")
                image = await client.get(result_url)
                if image.status_code >= 400 or not image.content:
                    raise RuntimeError("NanoBanana result image could not be downloaded")
                return image.content
            if flag in (2, 3):
                raise RuntimeError(data.get("errorMessage") or "NanoBanana generation failed")
        raise RuntimeError("NanoBanana staging timed out")
