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

from config import get_settings
from db import ProjectRecord, get_project, update_project
from services.staging import stage_image
from services.stitcher import stitch_images
from services.world_gen import delete_worlds, generate_world, world_display_name
from storage import delete_project_assets, get_storage, panorama_key, staged_key

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


async def run_stitch(project_id: str) -> None:
    update_project(project_id, status="stitching", error=None, active_job="stitch")
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
            if _deleted(project_id):
                return
            key = panorama_key(project_id)
            storage.put_file(key, output, "image/jpeg")
        saved = update_project(project_id, status="stitched", panorama_key=key, error=None, active_job=None)
        if saved is None:
            delete_project_assets(project_id)
            return
        logger.info("Project %s stitched", project_id)
    except Exception as exc:
        if _deleted(project_id):
            logger.info("Project %s deleted during stitch", project_id)
            return
        logger.exception("Project %s stitch failed", project_id)
        update_project(project_id, status="failed", error="Stitching failed. Try again.")
        return
    await run_world(project_id, source="original")


async def run_stage(project_id: str) -> None:
    update_project(project_id, status="staging", error=None, active_job="stage")
    try:
        project = _require(project_id)
        if not project.panorama_key or not project.staging_prompt:
            raise RuntimeError("No panorama available to stage")
        storage = get_storage()
        original = storage.get_bytes(project.panorama_key)
        staged_bytes, provider, notes = await stage_image(original, project.staging_prompt)
        if _deleted(project_id):
            return
        key = staged_key(project_id)
        storage.put_bytes(key, staged_bytes, "image/jpeg")
        saved = update_project(
            project_id,
            status="staged",
            staged_key=key,
            staging_provider=provider,
            staging_notes=notes,
            active_job=None,
            error=None,
        )
        if saved is None:
            delete_project_assets(project_id)
            return
        logger.info("Project %s staged via %s", project_id, provider)
    except Exception as exc:
        if _deleted(project_id):
            logger.info("Project %s deleted during staging", project_id)
            return
        logger.exception("Project %s staging failed", project_id)
        update_project(project_id, status="failed", error="Staging failed. Try again.")


async def run_world(project_id: str, source: str = "original") -> None:
    staged_source = source == "staged"
    update_project(
        project_id,
        status="generating_3d",
        error=None,
        active_job="staged_world" if staged_source else "world",
    )
    created_ids: list[str] = []
    try:
        project = _require(project_id)
        storage = get_storage()
        if staged_source:
            if not project.staged_key:
                raise RuntimeError("Stage the panorama before generating a 3D world from it")
            image = storage.get_bytes(project.staged_key)
        else:
            if not project.panorama_key:
                raise RuntimeError("No panorama available for a 3D world")
            image = storage.get_bytes(project.panorama_key)
        label = world_display_name(project.id, staged=staged_source)

        async def on_preview(result: dict) -> None:
            world_id = result.get("world_id")
            if world_id:
                created_ids.append(world_id)
            if _deleted(project_id):
                await delete_worlds([world_id])
                return
            if staged_source:
                update_project(
                    project_id,
                    staged_world_preview_url=result.get("marble_url"),
                    staged_world_preview_id=world_id,
                    staged_world_provider=result.get("provider"),
                )
            else:
                update_project(
                    project_id,
                    world_preview_url=result.get("marble_url"),
                    world_preview_id=world_id,
                    world_provider=result.get("provider"),
                )

        result = await generate_world(image, label, on_preview=on_preview)
        final_id = result.get("world_id")
        if final_id:
            created_ids.append(final_id)
        if _deleted(project_id):
            await delete_worlds(created_ids)
            return
        if staged_source:
            saved = update_project(
                project_id,
                status="complete",
                staged_world_provider=result.get("provider"),
                staged_world_id=result.get("world_id"),
                staged_world_marble_url=result.get("marble_url"),
                staged_world_splat_url=result.get("splat_url"),
                staged_world_mesh_url=result.get("mesh_url"),
                staged_world_thumbnail_url=result.get("thumbnail_url"),
                staged_world_operation_id=result.get("operation_id"),
                active_job=None,
                error=None,
            )
        else:
            saved = update_project(
                project_id,
                status="complete",
                world_provider=result.get("provider"),
                world_id=result.get("world_id"),
                world_marble_url=result.get("marble_url"),
                world_splat_url=result.get("splat_url"),
                world_mesh_url=result.get("mesh_url"),
                world_thumbnail_url=result.get("thumbnail_url"),
                world_operation_id=result.get("operation_id"),
                active_job=None,
                error=None,
            )
        if saved is None:
            await delete_worlds(created_ids)
            delete_project_assets(project_id)
            return
        logger.info("Project %s %s world ready via %s", project_id, source, result.get("provider"))
    except Exception as exc:
        if _deleted(project_id):
            logger.info("Project %s deleted during world generation", project_id)
            await delete_worlds(created_ids)
            return
        logger.exception("Project %s world generation failed", project_id)
        update_project(project_id, status="failed", error="3D generation failed. Try again.")


def _deleted(project_id: str) -> bool:
    return get_project(project_id) is None


def _require(project_id: str) -> ProjectRecord:
    project = get_project(project_id)
    if project is None:
        raise RuntimeError("Project not found")
    return project

