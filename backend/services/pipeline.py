"""In-process job pipeline for stitch, stage, and world generation.

Jobs are stored in the database before they run. A restart resumes them:
stitch and stage run again, and a World Labs operation that already has an id
is polled instead of generated again. Stitching uses a bounded thread pool so
CPU work does not stack, while world polling can proceed alongside it.

Set JOBS_INLINE=1 to await the work inside the request (used by tests).
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from config import get_settings
from db import (
    WORKER_ID,
    JobRecord,
    ProjectRecord,
    begin_job,
    claim_delayed_job,
    claim_job_lease,
    claim_resumable_jobs,
    delay_expired,
    delete_job,
    delete_owned_job,
    get_job,
    get_project,
    list_delayed_world_jobs,
    release_job_lease,
    renew_job_lease,
    touch_job,
    update_job,
    update_project,
)
from db import _as_utc as _as_utc
from services.quota import STAGING_QUOTA_MESSAGE, WORLD_QUOTA_MESSAGE
from services.staging import StagingProviderError, stage_image
from services.stitcher import stitch_images
from services.world_urls import valid_marble_url, valid_world_id
from services.world_gen import (
    DRAFT_WORLD_TIMEOUT_S,
    LeaseLost,
    FULL_WORLD_TIMEOUT_S,
    WORLD_DELAYED_MESSAGE,
    WORLD_DELAY_EXPIRED_MESSAGE,
    WORLD_FAILED_MESSAGE,
    WORLD_RATE_LIMIT_EXHAUSTED,
    OperationInFlight,
    ProviderFailure,
    RateLimitExceeded,
    _is_out_of_credits,
    bind_rate_limit_hook,
    reset_rate_limit_hook,
    continue_full_world,
    credits_required,
    delete_worlds,
    generate_world,
    generation_models,
    poll_world_operation,
    world_credits_balance,
    world_display_name,
)
from storage import delete_project_assets, get_storage, panorama_key, staged_key

logger = logging.getLogger(__name__)

_stitch_pool: ThreadPoolExecutor | None = None
_world_semaphore: asyncio.Semaphore | None = None
_in_flight: set[str] = set()
# Abort callbacks for jobs running in this process, by job id.
_abort_hooks: dict[str, object] = {}
# The job this task is running, so write guards can check its lease.
_current_job: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_job", default=None)
_INTERRUPTED_WORLD = "3D generation was interrupted. Retry from the app."
_INTERRUPTED_WORLD_DETAIL = (
    "Interrupted before a World Labs operation id was saved; not started again, to avoid a second charge."
)


def world_limit() -> asyncio.Semaphore:
    """How many World Labs jobs may run at once. Separate from worker concurrency."""
    global _world_semaphore
    if _world_semaphore is None:
        _world_semaphore = asyncio.Semaphore(get_settings().worldlabs_concurrency)
    return _world_semaphore


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
        # The worker slot is taken inside ``run_job``, after the lease and
        # heartbeat start, so a job waiting on the semaphore still renews it.
        task = asyncio.create_task(coro)
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


async def enqueue_job(
    project_id: str,
    kind: str,
    *,
    project_fields: dict | None = None,
    replace_delayed: bool = False,
    retire_worlds: str | None = None,
    start: bool = True,
) -> tuple[str, str | None]:
    """Claim the project and queue one job.

    The outcome is queued, busy, quota, or missing. The job id is set when a
    row was inserted.
    """
    outcome, job, retired_world_ids = begin_job(
        project_id,
        kind,
        project_fields=project_fields,
        replace_delayed=replace_delayed,
        retire_worlds=retire_worlds,
    )
    if outcome == "quota" and kind in {"world", "staged_world"}:
        update_project(project_id, error=WORLD_QUOTA_MESSAGE, active_job=None)
    elif outcome == "quota" and kind == "stage":
        current = get_project(project_id)
        if current is None or current.staging_status != "staged":
            update_project(project_id, staging_status="failed", staging_error=STAGING_QUOTA_MESSAGE, active_job=None)
    job_id = job.id if job is not None else None
    # begin_job cleared the retired world fields, skipped worlds other projects
    # still use, and queued the rest; the background retry pass deletes them.
    del retired_world_ids
    if outcome == "queued" and job_id is not None and start:
        await _schedule(job_id)
    return outcome, job_id


async def start_job(job_id: str) -> None:
    await _schedule(job_id)


async def resume_durable_jobs(*, fresh_running: bool = True) -> int:
    """Re-queue jobs whose lease is missing or expired.

    Delayed world jobs stay queued until ``retry_delayed_worlds`` sees credits.
    Jobs this process is already running are left alone, even when their
    ``updated_at`` is old. ``fresh_running`` is accepted so callers stay the same.
    """
    jobs = claim_resumable_jobs(fresh_running=fresh_running, skip_ids=set(_in_flight))
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


def _credits_to_resume(job: JobRecord) -> int:
    """Credits the next paid World Labs call needs. Polling an accepted op is free."""
    settings = get_settings()
    if job.operation_id and job.operation_phase == "full":
        return 0
    if job.operation_id and job.operation_phase == "draft":
        return credits_required(settings.worldlabs_model)
    models = generation_models(settings.worldlabs_draft_model, settings.worldlabs_model)
    return credits_required(models[0])


def _expire_delayed_job(job: JobRecord) -> None:
    staged_source = job.kind == "staged_world"
    delete_job(job.id)
    _fail_world(job.project_id, WORLD_DELAY_EXPIRED_MESSAGE, "delayed 3D exceeded the wait limit", staged_source)


async def retry_delayed_worlds() -> int:
    """Start delayed 3D jobs once the balance can pay for each next call.

    Jobs that have waited past the delay cap are failed instead. Claiming a job
    is an atomic status change, so a second replica does not start it too.
    """
    jobs = list_delayed_world_jobs()
    if not jobs:
        return 0
    settings = get_settings()
    if settings.worldlabs_api_key:
        balance = await world_credits_balance()
        if balance is None:
            balance = -1.0
    else:
        balance = float("inf")
    started = 0
    for job in jobs:
        if delay_expired(job.attempt_count, job.delayed_at):
            _expire_delayed_job(job)
            continue
        if balance < 0:
            continue
        needed = _credits_to_resume(job)
        if balance < needed:
            continue
        claimed = claim_delayed_job(job.id)
        if claimed is None:
            continue
        await _schedule(claimed.id)
        balance -= needed
        started += 1
    if started:
        logger.info("Released %d delayed 3D jobs after credits returned", started)
    return started


async def retry_delayed_project(project_id: str) -> str:
    """Run one project's delayed 3D job again. Does not charge quota a second time."""
    jobs = [job for job in list_delayed_world_jobs() if job.project_id == project_id]
    if not jobs:
        return "missing"
    job = jobs[0]
    if delay_expired(job.attempt_count, job.delayed_at):
        _expire_delayed_job(job)
        return "expired"
    claimed = claim_delayed_job(job.id)
    if claimed is None:
        return "busy"
    await _schedule(claimed.id)
    return "queued"


async def _schedule(job_id: str) -> None:
    if job_id in _in_flight:
        logger.info("Job %s is already running in this process", job_id)
        return
    if get_settings().jobs_inline:
        await run_job(job_id)
        return
    runner.submit(run_job(job_id))



def _require_lease(job_id: str | None) -> None:
    if not job_id:
        return
    job = get_job(job_id)
    now = datetime.now(timezone.utc)
    if (
        job is None
        or job.lease_owner != WORKER_ID
        or job.lease_expires_at is None
        or _as_utc(job.lease_expires_at) <= now
    ):
        raise LeaseLost()


def _guard_write(project_id: str) -> None:
    """Call before a storage write or DB update that follows a provider call.

    Raises LeaseLost when the project is gone or this process no longer owns
    the job, so a late provider result writes nothing.
    """
    if get_project(project_id) is None:
        raise LeaseLost()
    _require_lease(_current_job.get())


def _may_write(project_id: str) -> bool:
    try:
        _guard_write(project_id)
    except LeaseLost:
        return False
    return True


def abort_local_jobs(job_ids) -> int:
    """Cancel jobs this process is running (used by Delete all my data)."""
    stopped = 0
    for job_id in job_ids:
        hook = _abort_hooks.get(job_id)
        if hook is not None:
            hook()
            stopped += 1
    return stopped


def _lease_still_held(job_id: str) -> bool:
    job = get_job(job_id)
    if job is None or job.lease_owner != WORKER_ID or job.lease_expires_at is None:
        return False
    return _as_utc(job.lease_expires_at) > datetime.now(timezone.utc)


def _rate_limit_already_exceeded(job: JobRecord) -> bool:
    settings = get_settings()
    if job.rate_limit_attempts >= settings.worldlabs_rate_limit_max_attempts:
        return True
    if job.rate_limit_started_at is None:
        return False
    elapsed = (datetime.now(timezone.utc) - _as_utc(job.rate_limit_started_at)).total_seconds()
    return elapsed >= settings.worldlabs_rate_limit_max_seconds


def note_world_rate_limit(job_id: str) -> None:
    """Count one World Labs HTTP 429. Raise when the job's budget is spent."""
    job = get_job(job_id)
    if job is None:
        raise RateLimitExceeded("World Labs rate limit cap reached")
    now = datetime.now(timezone.utc)
    started = job.rate_limit_started_at or now
    attempts = job.rate_limit_attempts + 1
    update_job(job_id, rate_limit_attempts=attempts, rate_limit_started_at=started)
    settings = get_settings()
    elapsed = (now - _as_utc(started)).total_seconds()
    if attempts >= settings.worldlabs_rate_limit_max_attempts or elapsed >= settings.worldlabs_rate_limit_max_seconds:
        raise RateLimitExceeded(
            f"World Labs rate limit exceeded after {attempts} attempts and {int(elapsed)}s"
        )


def clear_world_rate_limit(job_id: str) -> None:
    """A successful poll or generate ends the current 429 window."""
    job = get_job(job_id)
    if job is None or (job.rate_limit_attempts == 0 and job.rate_limit_started_at is None):
        return
    update_job(job_id, rate_limit_attempts=0, rate_limit_started_at=None)


# How long to wait before retrying a lease renewal that raised.
_LEASE_RENEW_BACKOFF_SECONDS = 1.0


async def _lease_heartbeat(job_id: str, stop: asyncio.Event, on_lost) -> None:
    """Renew the lease for the whole run, including waits and provider sleeps.

    A transient database error is retried while this process still holds an
    unexpired lease. A real loss cancels the job so it cannot call a paid API.
    """
    interval = get_settings().job_lease_heartbeat_seconds
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            raise
        held = await _renew_lease(job_id, stop)
        if held is None:
            return
        if not held:
            logger.warning("Lost the lease for job %s", job_id)
            on_lost()
            return


async def _renew_lease(job_id: str, stop: asyncio.Event) -> bool | None:
    """True when the lease was renewed. None when the heartbeat was stopped."""
    while not stop.is_set():
        try:
            return renew_job_lease(job_id, WORKER_ID)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Lease renewal for job %s failed", job_id)
            try:
                still_held = _lease_still_held(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Both the renewal and the read behind it failed. We cannot prove
                # we still own the job, so stop it instead of running unleased.
                logger.exception(
                    "Could not read the lease for job %s after a failed renewal; stopping the job", job_id
                )
                return False
            if not still_held:
                return False
            try:
                await asyncio.wait_for(stop.wait(), timeout=_LEASE_RENEW_BACKOFF_SECONDS)
                return None
            except asyncio.TimeoutError:
                continue
    return None


async def run_job(job_id: str) -> None:
    if job_id in _in_flight:
        logger.info("Job %s is already running in this process", job_id)
        return
    _in_flight.add(job_id)
    stop = asyncio.Event()
    heart: asyncio.Task | None = None
    follow: str | None = None
    keep = False
    owned = False
    abandoned = False
    lost = False
    job_task = asyncio.current_task()

    def _on_lease_lost() -> None:
        nonlocal lost
        lost = True
        if job_task is not None and not job_task.done():
            job_task.cancel()

    _abort_hooks[job_id] = _on_lease_lost
    job_token = _current_job.set(job_id)
    try:
        job = claim_job_lease(job_id, WORKER_ID)
        if job is None:
            return
        owned = True
        heart = asyncio.create_task(_lease_heartbeat(job_id, stop, _on_lease_lost))
        async with runner._limit():
            try:
                _require_lease(job_id)
                if job.kind == "stitch":
                    follow = await run_stitch(job.project_id)
                elif job.kind == "stage":
                    await run_stage(job.project_id, resume=False, job_id=job_id)
                elif job.kind in {"world", "staged_world"}:
                    async with world_limit():
                        _require_lease(job_id)
                        source = "staged" if job.kind == "staged_world" else "original"
                        outcome = await run_world(job.project_id, source=source, job_id=job_id)
                        keep = outcome in {"delayed", "poll"}
                else:
                    logger.error("Unknown job kind %s", job.kind)
            except LeaseLost:
                abandoned = True
                logger.warning("Job %s lost its lease before a paid call; leaving it for the new owner", job_id)
            except asyncio.CancelledError:
                if lost:
                    abandoned = True
                    logger.warning("Job %s lost its lease; leaving it for the new owner", job_id)
                else:
                    raise
    finally:
        stop.set()
        if heart is not None:
            heart.cancel()
            try:
                await heart
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Lease heartbeat for job %s failed", job_id)
        _in_flight.discard(job_id)
        _abort_hooks.pop(job_id, None)
        _current_job.reset(job_token)
        if owned and not abandoned:
            if keep:
                release_job_lease(job_id, WORKER_ID)
            else:
                delete_owned_job(job_id, WORKER_ID)
    if follow and owned and not abandoned and not keep:
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
        pitches_raw = getattr(project, "pitches_json", None)
        pitches = json.loads(pitches_raw) if pitches_raw else None
        logger.info(
            "Stitch %s: %d images, hfov_deg=%s lens=%s yaws=%s pitches=%s",
            project_id, len(keys), project.hfov_deg, getattr(project, "lens", None), yaws, pitches,
        )
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
                lambda: stitch_images(paths, output, yaws_deg=yaws, pitches_deg=pitches, hfov_deg=hfov, refine=True),
            )
            if not ok or not output.is_file():
                raise RuntimeError("Stitching failed. Try recapturing with more overlap between photos.")
            if _deleted(project_id):
                return None
            _guard_write(project_id)
            key = panorama_key(project_id)
            storage.put_file(key, output, "image/jpeg")
        _guard_write(project_id)
        saved = update_project(project_id, status="stitched", panorama_key=key, error=None, active_job=None)
        if saved is None:
            delete_project_assets(project_id)
            return None
        logger.info("Project %s stitched", project_id)
    except LeaseLost:
        raise
    except Exception:
        if _deleted(project_id):
            logger.info("Project %s deleted during stitch", project_id)
            return None
        logger.exception("Project %s stitch failed", project_id)
        update_project(project_id, status="failed", error="Stitching failed. Try again.")
        return None
    return "world"


async def run_stage(project_id: str, *, resume: bool = False, job_id: str | None = None) -> None:
    """Stage without changing the capture status.

    The panorama and 3D world stay available when staging fails. Progress and
    errors live on staging_status and staging_error. A resumed job does not
    consume the daily quota again.
    """
    project = get_project(project_id)
    if project is None:
        return
    update_project(project_id, staging_status="staging", staging_error=None, provider_error=None)
    try:
        project = _require(project_id)
        if not project.panorama_key or not project.staging_prompt:
            raise RuntimeError("No panorama available to stage")
        storage = get_storage()
        original = storage.get_bytes(project.panorama_key)

        def before_generate() -> None:
            _require_lease(job_id)

        staged_bytes, provider, notes = await stage_image(
            original, project.staging_prompt, before_generate=before_generate
        )
        if _deleted(project_id):
            return
        _guard_write(project_id)
        key = staged_key(project_id)
        storage.put_bytes(key, staged_bytes, "image/jpeg")
        _guard_write(project_id)
        saved = update_project(
            project_id,
            staging_status="staged",
            staged_key=key,
            staging_provider=provider,
            **_clear_staged_resume(project_id),
            staging_notes=notes,
            staging_error=None,
        )
        if saved is None:
            delete_project_assets(project_id)
            return
        logger.info("Project %s staged via %s", project_id, provider)
    except LeaseLost:
        raise
    except StagingProviderError as exc:
        _fail_stage(project_id, exc.client_message, exc.detail)
    except Exception as exc:
        logger.exception("Project %s staging failed", project_id)
        _fail_stage(project_id, "Staging failed. Try again.", detail=str(exc)[:500])


def _fail_stage(project_id: str, message: str, detail: str | None = None) -> None:
    if _deleted(project_id):
        logger.info("Project %s deleted during staging", project_id)
        return
    if not _may_write(project_id):
        logger.warning("Project %s: not recording a staging failure without the lease", project_id)
        return
    client_message = message or "Staging failed. Try again."
    if detail:
        logger.error("Project %s staging provider error: %s", project_id, detail)
    else:
        logger.error("Project %s staging failed: %s", project_id, client_message)
    update_project(
        project_id,
        staging_status="failed",
        staging_error=client_message,
        provider_error=detail,
    )


async def run_world(
    project_id: str,
    source: str = "original",
    job_id: str | None = None,
    *,
    resume: bool = False,
) -> str | None:
    token = None
    if job_id:
        token = bind_rate_limit_hook(
            lambda: note_world_rate_limit(job_id),
            lambda: clear_world_rate_limit(job_id),
        )
    try:
        return await _run_world(project_id, source, job_id, resume=resume)
    finally:
        if token is not None:
            reset_rate_limit_hook(token)


async def _run_world(
    project_id: str,
    source: str,
    job_id: str | None,
    *,
    resume: bool,
) -> str | None:
    staged_source = source == "staged"
    job = get_job(job_id) if job_id else None
    existing = get_project(project_id)
    if existing is None:
        return
    if job and _rate_limit_already_exceeded(job):
        _record_paid_operation(project_id, job_id, staged_source)
        _fail_world(
            project_id,
            WORLD_RATE_LIMIT_EXHAUSTED,
            "World Labs rate limit cap reached before another provider call",
            staged_source,
        )
        return None
    if job and job.operation_phase == "submitting":
        _fail_world(project_id, _INTERRUPTED_WORLD, _INTERRUPTED_WORLD_DETAIL, staged_source)
        return None
    if job and job.operation_id and job.operation_phase in {"draft", "full"}:
        return await _resume_world(project_id, job, staged_source)
    paid = _take_paid_operation(existing, staged_source)
    if job and paid:
        logger.warning(
            "Project %s resuming paid World Labs operation %s (%s) instead of generating again",
            project_id,
            paid["operation_id"],
            paid["phase"],
        )
        resumed = update_job(
            job.id,
            operation_id=paid["operation_id"],
            operation_phase=paid["phase"],
            media_asset_id=paid.get("media_asset_id") or job.media_asset_id,
            status="running",
        )
        if resumed is not None:
            return await _resume_world(project_id, resumed, staged_source)

    del resume
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
            _require_lease(job_id)
            if job_id:
                update_job(job_id, operation_phase="submitting", status="running")

        async def on_operation(operation_id: str, phase: str) -> None:
            _remember_operation(project_id, job_id, operation_id, phase, staged_source)

        def on_tick() -> None:
            if job_id:
                touch_job(job_id)

        result = await generate_world(
            image,
            label,
            on_preview=on_preview,
            on_media=on_media,
            on_operation=on_operation,
            on_submitting=on_submitting,
            on_tick=on_tick,
        )
        await _store_world(project_id, result, staged_source, created_ids)
    except LeaseLost:
        raise
    except RateLimitExceeded as exc:
        _record_paid_operation(project_id, job_id, staged_source)
        await _fail_world_clean(project_id, created_ids, exc.client_message, exc.detail, staged_source)
        return None
    except OperationInFlight:
        if job_id and (current := get_job(job_id)) and _rate_limit_already_exceeded(current):
            _record_paid_operation(project_id, job_id, staged_source)
            await _fail_world_clean(
                project_id,
                created_ids,
                WORLD_RATE_LIMIT_EXHAUSTED,
                "World Labs rate limit cap reached",
                staged_source,
            )
            return None
        held = _hold_charged_operation(project_id, job_id, staged_source)
        if held:
            return held
        await _fail_world_clean(project_id, created_ids, WORLD_FAILED_MESSAGE, None, staged_source)
    except ProviderFailure as exc:
        if isinstance(exc, RateLimitExceeded):
            _record_paid_operation(project_id, job_id, staged_source)
            await _fail_world_clean(project_id, created_ids, exc.client_message, exc.detail, staged_source)
            return None
        if exc.status_code == 429:
            held = _hold_charged_operation(project_id, job_id, staged_source)
            if held:
                return held
        if _is_out_of_credits(exc):
            return _delay_world(project_id, job_id, exc.detail, staged_source)
        await _fail_world_clean(project_id, created_ids, exc.client_message, exc.detail, staged_source)
    except Exception:
        await _fail_world_clean(project_id, created_ids, WORLD_FAILED_MESSAGE, None, staged_source)
    return None


async def _resume_world(project_id: str, job: JobRecord, staged_source: bool) -> str | None:
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

        def on_tick() -> None:
            touch_job(job.id)

        parsed = await poll_world_operation(job.operation_id or "", timeout_s, on_tick=on_tick)
        if phase == "draft":
            _store_preview(project_id, parsed, staged_source, created_ids)
            if _deleted(project_id):
                await delete_worlds(created_ids)
                return None
            image, label = _world_inputs(project, staged_source)
            _require_lease(job.id)

            async def on_submitting(_phase: str) -> None:
                _require_lease(job.id)
                update_job(job.id, operation_phase="submitting", status="running")

            async def on_operation(operation_id: str, next_phase: str) -> None:
                _remember_operation(project_id, job.id, operation_id, next_phase, staged_source)

            parsed = await continue_full_world(
                image,
                label,
                job.media_asset_id,
                on_operation=on_operation,
                on_submitting=on_submitting,
                on_tick=on_tick,
            )
        await _store_world(project_id, parsed, staged_source, created_ids)
    except LeaseLost:
        raise
    except RateLimitExceeded as exc:
        _record_paid_operation(project_id, job.id, staged_source)
        await _fail_world_clean(project_id, created_ids, exc.client_message, exc.detail, staged_source)
        return None
    except OperationInFlight:
        if _rate_limit_already_exceeded(get_job(job.id) or job):
            _record_paid_operation(project_id, job.id, staged_source)
            await _fail_world_clean(
                project_id,
                created_ids,
                WORLD_RATE_LIMIT_EXHAUSTED,
                "World Labs rate limit cap reached",
                staged_source,
            )
            return None
        held = _hold_charged_operation(project_id, job.id, staged_source)
        if held:
            return held
        await _fail_world_clean(project_id, created_ids, WORLD_FAILED_MESSAGE, None, staged_source)
    except ProviderFailure as exc:
        if isinstance(exc, RateLimitExceeded):
            _record_paid_operation(project_id, job.id, staged_source)
            await _fail_world_clean(project_id, created_ids, exc.client_message, exc.detail, staged_source)
            return None
        if exc.status_code == 429:
            held = _hold_charged_operation(project_id, job.id, staged_source)
            if held:
                return held
        if _is_out_of_credits(exc):
            return _delay_world(project_id, job.id, exc.detail, staged_source)
        await _fail_world_clean(project_id, created_ids, exc.client_message, exc.detail, staged_source)
    except Exception:
        await _fail_world_clean(project_id, created_ids, WORLD_FAILED_MESSAGE, None, staged_source)
    return None


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


def _accepted_world(result: dict) -> tuple[str | None, str | None]:
    world_id = valid_world_id(result.get("world_id"))
    marble = valid_marble_url(result.get("marble_url"))
    if marble is None and world_id:
        marble = f"https://marble.worldlabs.ai/world/{world_id}"
    return world_id, marble


def _store_preview(project_id: str, result: dict, staged_source: bool, created_ids: list[str]) -> None:
    world_id, marble = _accepted_world(result)
    if world_id:
        created_ids.append(world_id)
    if _deleted(project_id):
        return
    _guard_write(project_id)
    if staged_source:
        update_project(
            project_id,
            staged_world_preview_url=marble,
            staged_world_preview_id=world_id,
            staged_world_provider=result.get("provider"),
        )
    else:
        update_project(
            project_id,
            world_preview_url=marble,
            world_preview_id=world_id,
            world_provider=result.get("provider"),
        )


async def _store_world(project_id: str, result: dict, staged_source: bool, created_ids: list[str]) -> None:
    final_id, marble = _accepted_world(result)
    if final_id:
        created_ids.append(final_id)
    if _deleted(project_id):
        await delete_worlds(created_ids)
        return
    _guard_write(project_id)
    operation_id = result.get("operation_id")
    stored_operation = operation_id[:64] if isinstance(operation_id, str) else None
    if staged_source:
        saved = update_project(
            project_id,
            status="complete",
            staged_world_provider=result.get("provider"),
            staged_world_id=final_id,
            staged_world_marble_url=marble,
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
            world_id=final_id,
            world_marble_url=marble,
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


def _hold_charged_operation(project_id: str, job_id: str | None, staged_source: bool) -> str | None:
    """Leave the job queued so the next run polls the operation World Labs already accepted."""
    _guard_write(project_id)
    if not job_id:
        return None
    job = get_job(job_id)
    if job is None or not job.operation_id:
        return None
    if _rate_limit_already_exceeded(job):
        return None
    phase = job.operation_phase
    if phase == "submitting":
        phase = "draft"
    if phase not in {"draft", "full"}:
        return None
    logger.warning(
        "World Labs rate limited project %s; polling operation %s (%s) instead of generating again",
        project_id,
        job.operation_id,
        phase,
    )
    update_job(job_id, status="pending", operation_id=job.operation_id, operation_phase=phase)
    update_project(
        project_id,
        status="generating_3d",
        error=None,
        provider_error=f"HTTP 429: polling operation {job.operation_id}",
        active_job="staged_world" if staged_source else "world",
    )
    return "poll"


def _record_paid_operation(project_id: str, job_id: str | None, staged_source: bool) -> None:
    """Keep an operation World Labs already charged for when the job fails on the 429 cap.

    A later retry for the same source polls it (and continues to full from a
    draft) instead of calling worlds:generate again.
    """
    if not job_id:
        return
    _guard_write(project_id)
    job = get_job(job_id)
    if job is None or not job.operation_id:
        return
    phase = job.operation_phase
    if phase == "submitting":
        phase = "draft"
    if phase not in {"draft", "full"}:
        return
    project = get_project(project_id)
    image_hash = _source_image_hash(project, staged_source) if project is not None else None
    if image_hash is None:
        logger.warning("Project %s: no source image to fingerprint; not recording operation %s", project_id, job.operation_id)
        return
    payload = {
        "source": "staged" if staged_source else "original",
        "image_sha256": image_hash,
        "operation_id": job.operation_id,
        "phase": phase,
        "media_asset_id": job.media_asset_id,
    }
    update_project(project_id, resume_operation=json.dumps(payload))
    logger.warning(
        "Project %s failed on the World Labs 429 cap; recorded paid operation %s (%s) for retry",
        project_id,
        job.operation_id,
        phase,
    )


def _take_paid_operation(project: ProjectRecord, staged_source: bool) -> dict | None:
    if not project.resume_operation:
        return None
    try:
        payload = json.loads(project.resume_operation)
    except (TypeError, ValueError):
        update_project(project.id, resume_operation=None)
        return None
    if payload.get("source") != ("staged" if staged_source else "original"):
        return None
    update_project(project.id, resume_operation=None)
    if not payload.get("operation_id") or payload.get("phase") not in {"draft", "full"}:
        return None
    current = _source_image_hash(project, staged_source)
    if not current or payload.get("image_sha256") != current:
        logger.warning(
            "Project %s: source image changed since operation %s was paid for; starting fresh",
            project.id,
            payload.get("operation_id"),
        )
        return None
    return payload


def _source_image_hash(project: ProjectRecord, staged_source: bool) -> str | None:
    """SHA-256 of the image a 3D run for this source would send to World Labs."""
    try:
        image, _ = _world_inputs(project, staged_source)
    except Exception:
        return None
    return hashlib.sha256(image).hexdigest()


def _clear_staged_resume(project_id: str) -> dict:
    """A new staged image invalidates a saved staged operation. An original one is kept."""
    project = get_project(project_id)
    if project is None or not project.resume_operation:
        return {}
    try:
        source = json.loads(project.resume_operation).get("source")
    except (TypeError, ValueError, AttributeError):
        return {"resume_operation": None}
    return {"resume_operation": None} if source == "staged" else {}


def _remember_operation(project_id: str, job_id: str | None, operation_id: str, phase: str, staged_source: bool) -> None:
    _guard_write(project_id)
    if job_id:
        update_job(job_id, operation_id=operation_id, operation_phase=phase, status="running")
    field = "staged_world_operation_id" if staged_source else "world_operation_id"
    update_project(project_id, **{field: operation_id[:64]})
    logger.info("Project %s World Labs operation %s (%s)", project_id, operation_id, phase)


def _delay_world(project_id: str, job_id: str | None, detail: str, staged_source: bool) -> str | None:
    """Keep the panorama and the job. A later credit check runs the job again.

    Returns ``delayed`` while the job should stay queued, or None when the wait
    cap has been reached and the project is failed.
    """
    from datetime import datetime, timezone

    _guard_write(project_id)
    phase = None
    operation_id = None
    attempts = 1
    delayed_at = datetime.now(timezone.utc)
    job = get_job(job_id) if job_id else None
    if job is not None:
        phase = job.operation_phase
        operation_id = job.operation_id
        if phase == "submitting":
            phase = "draft" if operation_id else None
        attempts = (job.attempt_count or 0) + 1
        delayed_at = job.delayed_at or delayed_at
        if delay_expired(attempts, delayed_at):
            logger.error("Project %s 3D delay expired: %s", project_id, detail)
            delete_job(job_id or "")
            _fail_world(project_id, WORLD_DELAY_EXPIRED_MESSAGE, detail, staged_source)
            return None
        update_job(
            job_id or "",
            status="delayed",
            operation_phase=phase,
            attempt_count=attempts,
            delayed_at=delayed_at,
        )
    logger.error("Project %s 3D delayed: %s", project_id, detail)
    update_project(
        project_id,
        status="delayed",
        error=WORLD_DELAYED_MESSAGE,
        provider_error=detail,
        active_job="staged_world" if staged_source else "world",
    )
    return "delayed"


def _fail_world(project_id: str, message: str, detail: str | None, staged_source: bool) -> None:
    if not _may_write(project_id):
        logger.warning("Project %s: not recording a 3D failure without the project or lease", project_id)
        return
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
