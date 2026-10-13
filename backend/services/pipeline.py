"""In-process job pipeline for stitch, stage, and world generation.

Jobs run on the server event loop, with stitching offloaded to a thread.
References to the tasks are retained so they are not garbage-collected.
Set JOBS_INLINE=1 to await the work inside the request (used by tests).
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from config import get_settings, is_public_base_url
from db import ProjectRecord, get_project, update_project
from services.staging import stage_image
from services.stitcher import stitch_images
from services.world_gen import generate_world
from storage import get_storage, panorama_key, staged_key

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._semaphore: asyncio.Semaphore | None = None

    def _limit(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(get_settings().stitch_concurrency)
        return self._semaphore

    def submit(self, coro) -> asyncio.Task:
        async def wrapped() -> None:
            async with self._limit():
                await coro

        task = asyncio.create_task(wrapped())
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error:
            logger.error("Background job crashed: %s", error)


runner = JobRunner()


async def enqueue(coro) -> None:
    if get_settings().jobs_inline:
        await coro
        return
    runner.submit(coro)


def public_original_url(project: ProjectRecord) -> str | None:
    """URL NanoBanana can fetch. Presigned R2 first, then a public share URL."""
    if not project.panorama_key:
        return None
    signed = get_storage().presign_get(project.panorama_key, expires=7200)
    if signed:
        return signed
    base = get_settings().public_base_url
    if is_public_base_url(base):
        return f"{base.rstrip('/')}/v/{project.share_token}/panorama.jpg"
    return None


async def run_stitch(project_id: str) -> None:
    update_project(project_id, status="stitching", error=None)
    try:
        project = _require(project_id)
        storage = get_storage()
        keys = sorted(
            key
            for key in storage.list_keys(f"uploads/{project_id}/")
            if key.lower().endswith((".jpg", ".jpeg", ".png"))
        )
        if len(keys) < 2:
            raise RuntimeError("Not enough images to stitch")
        yaws = json.loads(project.yaws_json) if project.yaws_json else None
        with tempfile.TemporaryDirectory(prefix="360flow-stitch-") as tmp:
            root = Path(tmp)
            paths: list[Path] = []
            for index, key in enumerate(keys):
                dest = root / f"{index:02d}.jpg"
                storage.materialize(key, dest)
                paths.append(dest)
            output = root / "panorama.jpg"
            loop = asyncio.get_running_loop()
            hfov = project.hfov_deg
            ok = await loop.run_in_executor(
                None,
                lambda: stitch_images(paths, output, yaws_deg=yaws, hfov_deg=hfov, refine=True),
            )
            if not ok or not output.is_file():
                raise RuntimeError("Stitching failed. Try recapturing with more overlap between photos.")
            key = panorama_key(project_id)
            storage.put_file(key, output, "image/jpeg")
        update_project(project_id, status="stitched", panorama_key=key, error=None)
        logger.info("Project %s stitched", project_id)
    except Exception as exc:
        logger.exception("Project %s stitch failed", project_id)
        update_project(project_id, status="failed", error=_short(exc))


async def run_stage(project_id: str) -> None:
    update_project(project_id, status="staging", error=None)
    try:
        project = _require(project_id)
        if not project.panorama_key or not project.staging_prompt:
            raise RuntimeError("No panorama available to stage")
        storage = get_storage()
        original = storage.get_bytes(project.panorama_key)
        staged_bytes, provider = await stage_image(
            original,
            project.staging_prompt,
            public_original_url(project),
        )
        key = staged_key(project_id)
        storage.put_bytes(key, staged_bytes, "image/jpeg")
        update_project(project_id, status="staged", staged_key=key, staging_provider=provider, error=None)
        logger.info("Project %s staged via %s", project_id, provider)
    except Exception as exc:
        logger.exception("Project %s staging failed", project_id)
        update_project(project_id, status="failed", error=_short(exc))


async def run_world(project_id: str) -> None:
    update_project(project_id, status="generating_3d", error=None)
    try:
        project = _require(project_id)
        if not project.staged_key:
            raise RuntimeError("Stage the panorama before generating a 3D world")
        image = get_storage().get_bytes(project.staged_key)
        result = await generate_world(image, project.name)
        update_project(
            project_id,
            status="complete",
            world_provider=result.get("provider"),
            world_id=result.get("world_id"),
            world_marble_url=result.get("marble_url"),
            world_splat_url=result.get("splat_url"),
            world_mesh_url=result.get("mesh_url"),
            world_thumbnail_url=result.get("thumbnail_url"),
            world_operation_id=result.get("operation_id"),
            error=None,
        )
        logger.info("Project %s world ready via %s", project_id, result.get("provider"))
    except Exception as exc:
        logger.exception("Project %s world generation failed", project_id)
        update_project(project_id, status="failed", error=_short(exc))


def _require(project_id: str) -> ProjectRecord:
    project = get_project(project_id)
    if project is None:
        raise RuntimeError("Project not found")
    return project


def _short(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:500]
