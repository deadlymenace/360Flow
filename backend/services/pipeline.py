"""In-process job pipeline for stitch, stage, and world generation.

Jobs are stored in the database before they run. A restart resumes them:
stitch and stage run again, and a World Labs operation that already has an id
is polled instead of generated again. Stitching uses a bounded thread pool so
CPU work does not stack, while world polling can proceed alongside it.

Set JOBS_INLINE=1 to await the work inside the request (used by tests).
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config import get_settings
from db import (
    JobRecord,
    ProjectRecord,
    claim_resumable_jobs,
    create_job,
    delete_job,
    get_job,
    get_project,
    update_job,
    update_project,
)
from services.quota import (
    STAGING_QUOTA_MESSAGE,
    WORLD_QUOTA_MESSAGE,
    consume_staging_quota,
    consume_world_quota,
)
from services.staging import StagingProviderError, stage_image
from services.stitcher import stitch_images
from services.world_gen import (
    DRAFT_WORLD_TIMEOUT_S,
    FULL_WORLD_TIMEOUT_S,
    WORLD_FAILED_MESSAGE,
    ProviderFailure,
    continue_full_world,
    delete_worlds,
    generate_world,
    poll_world_operation,
    world_display_name,
)
from storage import delete_project_assets, get_storage, panorama_key, staged_key

logger = logging.getLogger(__name__)

_stitch_pool: ThreadPoolExecutor | None = None
_INTERRUPTED_WORLD = "3D generation was interrupted. Retry from the app."
_INTERRUPTED_WORLD_DETAIL = (
    "Interrupted before a World Labs operation id was saved; not started again, to avoid a second charge."
)


def stitch_executor() -> ThreadPoolExecutor:
    """CPU pool for panorama stitching. Separate from the async job limit."""
    global _stitch_pool
    if _stitch_pool is None:
        workers = get_settings().stitch_concurrency
        _stitch_pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stitch")
    return _stitch_pool


class JobRunner:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._semaphore: asyncio.Semaphore | None = None

    def _limit(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(get_settings().worker_concurrency)
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


async def enqueue_job(project_id: str, kind: str) -> None:
    job = create_job(project_id, kind)
    await _schedule(job.id)


async def resume_durable_jobs() -> int:
    """Re-queue jobs that were pending or running when the process stopped."""
    jobs = claim_resumable_jobs()
    for job in jobs:
        logger.warning(
            "Resuming %s job %s for project %s (operation %s, phase %s)",
            job.kind,
            job.id,
            job.project_id,
            job.operation_id,
            job.operation_phase,
        )
        await _schedule(job.id)
    return len(jobs)


async def _schedule(job_id: str) -> None:
    if get_settings().jobs_inline:
        await run_job(job_id)
        return
    runner.submit(run_job(job_id))


async def run_job(job_id: str) -> None:
    job = get_job(job_id)
    if job is None:
        return
    resume = job.status == "running"
    update_job(job_id, status="running")
    follow: str | None = None
    try:
        if job.kind == "stitch":
            follow = await run_stitch(job.project_id)
        elif job.kind == "stage":
            await run_stage(job.project_id, resume=resume)
        elif job.kind == "world":
            await run_world(job.project_id, source="original", job_id=job_id, resume=resume)
        elif job.kind == "staged_world":
            await run_world(job.project_id, source="staged", job_id=job_id, resume=resume)
        else:
            logger.error("Unknown job kind %s", job.kind)
    finally:
        delete_job(job_id)
    if follow:
        await enqueue_job(job.project_id, follow)


async def run_stitch(project_id: str) -> str | None:
    """Stitch the upload. Returns ``world`` when a 3D job should follow."""
    update_project(project_id, status="stitching", error=None, active_job="stitch", provider_error=None)
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
            pool = stitch_executor()
            ok = await loop.run_in_executor(
                pool,
                lambda: stitch_images(paths, output, yaws_deg=yaws, hfov_deg=hfov, refine=True),
            )
            if not ok or not output.is_file():
                raise RuntimeError("Stitching failed. Try recapturing with more overlap between photos.")
            if _deleted(project_id):
                return None
            key = panorama_key(project_id)
            storage.put_file(key, output, "image/jpeg")
        saved = update_project(project_id, status="stitched", panorama_key=key, error=None, active_job=None)
        if saved is None:
            delete_project_assets(project_id)
            return None
        logger.info("Project %s stitched", project_id)
    except Exception:
        if _deleted(project_id):
            logger.info("Project %s deleted during stitch", project_id)
            return None
        logger.exception("Project %s stitch failed", project_id)
        update_project(project_id, status="failed", error="Stitching failed. Try again.")
        return None
    return "world"


async def run_stage(project_id: str, *, resume: bool = False) -> None:
    """Stage without changing the capture status.

    The panorama and 3D world stay available when staging fails. Progress and
    errors live on staging_status and staging_error. A resumed job does not
    consume the daily quota again.
    """
    project = get_project(project_id)
    if project is None:
        return
    if not resume and not consume_staging_quota(project.owner_device_id):
        update_project(project_id, staging_status="failed", staging_error=STAGING_QUOTA_MESSAGE)
        return
    update_project(project_id, staging_status="staging", staging_error=None, provider_error=None)
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
            staging_status="staged",
            staged_key=key,
            staging_provider=provider,
            staging_notes=notes,
            staging_error=None,
        )
        if saved is None:
            delete_project_assets(project_id)
            return
        logger.info("Project %s staged via %s", project_id, provider)
    except StagingProviderError as exc:
        _fail_stage(project_id, exc.client_message, exc.detail)
    except Exception as exc:
        logger.exception("Project %s staging failed", project_id)
        _fail_stage(project_id, "Staging failed. Try again.", detail=str(exc)[:500])


def _fail_stage(project_id: str, message: str, detail: str | None = None) -> None:
    if _deleted(project_id):
        logger.info("Project %s deleted during staging", project_id)
        return
    if detail:
        logger.error("Project %s staging provider error: %s", project_id, detail)
        update_project(
            project_id,
            staging_status="failed",
            staging_error=message,
            provider_error=detail,
        )


async def run_world(
    project_id: str,
    source: str = "original",
    job_id: str | None = None,
    *,
    resume: bool = False,
) -> None:
    staged_source = source == "staged"
    job = get_job(job_id) if job_id else None
    existing = get_project(project_id)
    if existing is None:
        return
    if job and job.operation_phase == "submitting":
        _fail_world(project_id, _INTERRUPTED_WORLD, _INTERRUPTED_WORLD_DETAIL, staged_source)
        return
    if job and job.operation_id and job.operation_phase in {"draft", "full"}:
        await _resume_world(project_id, job, staged_source)
        return

    already_started = resume or (
        existing.status == "generating_3d" and existing.active_job in {"world", "staged_world"}
    )
    if not already_started and not consume_world_quota(existing.owner_device_id):
        logger.info("Project %s skipped a paid world; daily quota is spent", project_id)
        update_project(project_id, error=WORLD_QUOTA_MESSAGE, active_job=None)
        return
    update_project(
        project_id,
        status="generating_3d",
        error=None,
        provider_error=None,
        active_job="staged_world" if staged_source else "world",
    )
    created_ids: list[str] = []
    try:
        project = _require(project_id)
        image, label = _world_inputs(project, staged_source)

        async def on_preview(result: dict) -> None:
            _store_preview(project_id, result, staged_source, created_ids)

        async def on_media(media_id: str) -> None:
            if job_id:
                update_job(job_id, media_asset_id=media_id)

        async def on_submitting(phase: str) -> None:
            if job_id:
                update_job(job_id, operation_phase="submitting", status="running")

        async def on_operation(operation_id: str, phase: str) -> None:
            _remember_operation(project_id, job_id, operation_id, phase, staged_source)

        result = await generate_world(
            image,
            label,
            on_preview=on_preview,
            on_media=on_media,
            on_operation=on_operation,
            on_submitting=on_submitting,
        )
        await _store_world(project_id, result, staged_source, created_ids)
    except ProviderFailure as exc:
        await _fail_world_clean(project_id, created_ids, exc.client_message, exc.detail, staged_source)
    except Exception:
        await _fail_world_clean(project_id, created_ids, WORLD_FAILED_MESSAGE, None, staged_source)


async def _resume_world(project_id: str, job: JobRecord, staged_source: bool) -> None:
    """Poll the saved operation. A finished draft continues into the full model."""
    update_project(
        project_id,
        status="generating_3d",
        error=None,
        active_job="staged_world" if staged_source else "world",
    )
    created_ids: list[str] = []
    try:
        project = _require(project_id)
        phase = job.operation_phase or "full"
        timeout_s = DRAFT_WORLD_TIMEOUT_S if phase == "draft" else FULL_WORLD_TIMEOUT_S
        logger.info("Re-polling World Labs operation %s (%s) for project %s", job.operation_id, phase, project_id)
        parsed = await poll_world_operation(job.operation_id or "", timeout_s)
        if phase == "draft":
            _store_preview(project_id, parsed, staged_source, created_ids)
            if _deleted(project_id):
                await delete_worlds(created_ids)
                return
            image, label = _world_inputs(project, staged_source)

            async def on_submitting(_phase: str) -> None:
                update_job(job.id, operation_phase="submitting", status="running")

            async def on_operation(operation_id: str, next_phase: str) -> None:
                _remember_operation(project_id, job.id, operation_id, next_phase, staged_source)

            parsed = await continue_full_world(
                image,
                label,
                job.media_asset_id,
                on_operation=on_operation,
                on_submitting=on_submitting,
            )
        await _store_world(project_id, parsed, staged_source, created_ids)
    except ProviderFailure as exc:
        await _fail_world_clean(project_id, created_ids, exc.client_message, exc.detail, staged_source)
    except Exception:
        await _fail_world_clean(project_id, created_ids, WORLD_FAILED_MESSAGE, None, staged_source)


def _world_inputs(project: ProjectRecord, staged_source: bool) -> tuple[bytes, str]:
    storage = get_storage()
    if staged_source:
        if not project.staged_key:
            raise RuntimeError("Stage the panorama before generating a 3D world from it")
        image = storage.get_bytes(project.staged_key)
    else:
        if not project.panorama_key:
            raise RuntimeError("No panorama available for a 3D world")
        image = storage.get_bytes(project.panorama_key)
    return image, world_display_name(project.id, staged=staged_source)


def _store_preview(project_id: str, result: dict, staged_source: bool, created_ids: list[str]) -> None:
    world_id = result.get("world_id")
    if world_id:
        created_ids.append(world_id)
    if _deleted(project_id):
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


async def _store_world(project_id: str, result: dict, staged_source: bool, created_ids: list[str]) -> None:
    final_id = result.get("world_id")
    if final_id:
        created_ids.append(final_id)
    if _deleted(project_id):
        await delete_worlds(created_ids)
        return
    operation_id = result.get("operation_id")
    stored_operation = operation_id[:64] if isinstance(operation_id, str) else None
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
            staged_world_operation_id=stored_operation,
            active_job=None,
            error=None,
            provider_error=None,
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
            world_operation_id=stored_operation,
            active_job=None,
            error=None,
            provider_error=None,
        )
    if saved is None:
        await delete_worlds(created_ids)
        delete_project_assets(project_id)
        return
    logger.info("Project %s %s world ready via %s", project_id, "staged" if staged_source else "original", result.get("provider"))


def _remember_operation(project_id: str, job_id: str | None, operation_id: str, phase: str, staged_source: bool) -> None:
    if job_id:
        update_job(job_id, operation_id=operation_id, operation_phase=phase, status="running")
    field = "staged_world_operation_id" if staged_source else "world_operation_id"
    update_project(project_id, **{field: operation_id[:64]})
    logger.info("Project %s World Labs operation %s (%s)", project_id, operation_id, phase)


def _fail_world(project_id: str, message: str, detail: str | None, staged_source: bool) -> None:
    if detail:
        logger.error("Project %s world provider error: %s", project_id, detail)
    else:
        logger.exception("Project %s world generation failed", project_id)
    update_project(
        project_id,
        status="failed",
        error=message,
        provider_error=detail,
        active_job="staged_world" if staged_source else "world",
    )


async def _fail_world_clean(
    project_id: str,
    created_ids: list[str],
    message: str,
    detail: str | None,
    staged_source: bool,
) -> None:
    if _deleted(project_id):
        logger.info("Project %s deleted during world generation", project_id)
        await delete_worlds(created_ids)
        return
    _fail_world(project_id, message, detail, staged_source)


def _deleted(project_id: str) -> bool:
    return get_project(project_id) is None


def _require(project_id: str) -> ProjectRecord:
    project = get_project(project_id)
    if project is None:
        raise RuntimeError("Project not found")
    return project
