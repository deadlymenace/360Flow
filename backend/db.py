"""Project persistence. Postgres when DATABASE_URL is set, otherwise SQLite."""

from __future__ import annotations

import hashlib
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator
from uuid import uuid4

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, create_engine, inspect, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config import Settings, get_settings

TRANSIENT_STATUSES = ("uploading", "stitching", "staging", "generating_3d", "delayed")
# Statuses that still have a worker. Delayed is queued, not running, but it
# still occupies the one open job slot for the project.
OPEN_JOB_STATUSES = ("pending", "running", "delayed")
GLOBAL_QUOTA_DEVICE = "__global__"
_RESUME_LOCK_KEY = 360360160
# One id per process. A lease names this owner so another worker can tell a
# live job from one whose process died.
WORKER_ID = str(uuid4())


class Base(DeclarativeBase):
    pass


class DeviceRow(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    staging_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    staging_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    world_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    share_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    share_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    hfov_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    yaws_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    lens: Mapped[str | None] = mapped_column(String(64), nullable=True)
    panorama_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    staged_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    world_marble_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_splat_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_mesh_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    world_preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_preview_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    staged_world_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    staged_world_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    staged_world_marble_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    staged_world_preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    staged_world_splat_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    staged_world_mesh_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    staged_world_thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    staged_world_operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    staged_world_preview_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    staging_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    staging_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    staging_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_job: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Null and 0 both mean the staged view is private. 1 means the owner published it.
    staged_published: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preview_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    preview_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobRow(Base):
    """A stitch, stage, or world job that must survive a process restart."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "jobs_one_open_per_project",
            "project_id",
            unique=True,
            sqlite_where=text("status IN ('pending', 'running', 'delayed')"),
            postgresql_where=text("status IN ('pending', 'running', 'delayed')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    operation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_phase: Mapped[str | None] = mapped_column(String(16), nullable=True)
    media_asset_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    delayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rate_limit_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    rate_limit_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DailyUsageRow(Base):
    __tablename__ = "daily_usage"

    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class RateWindowRow(Base):
    """Fixed-window counters shared by every replica through the database."""

    __tablename__ = "rate_windows"

    bucket: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class PendingWorldDeleteRow(Base):
    __tablename__ = "pending_world_deletes"

    world_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PendingDraftCleanupRow(Base):
    """A draft operation World Labs accepted, then the full model continued without it."""

    __tablename__ = "pending_draft_cleanups"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Types in this map are sent as raw DDL to both SQLite and Postgres.
# TIMESTAMP is valid in both. DATETIME is not a Postgres type and crashes startup.
_EXTRA_COLUMNS = {
    "world_preview_url": "TEXT",
    "world_preview_id": "VARCHAR(64)",
    "staged_world_provider": "VARCHAR(32)",
    "staged_world_id": "VARCHAR(64)",
    "staged_world_marble_url": "TEXT",
    "staged_world_preview_url": "TEXT",
    "staged_world_splat_url": "TEXT",
    "staged_world_mesh_url": "TEXT",
    "staged_world_thumbnail_url": "TEXT",
    "staged_world_operation_id": "VARCHAR(64)",
    "staging_notes": "TEXT",
    "staging_status": "VARCHAR(32)",
    "staging_error": "TEXT",
    "active_job": "VARCHAR(32)",
    "owner_device_id": "VARCHAR(36)",
    "staged_world_preview_id": "VARCHAR(64)",
    "share_expires_at": "TIMESTAMP",
    "provider_error": "TEXT",
    "staged_published": "INTEGER",
    "preview_token": "VARCHAR(64)",
    "preview_expires_at": "TIMESTAMP",
}

_JOB_EXTRA_COLUMNS = {
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
    "delayed_at": "TIMESTAMP",
    "lease_owner": "VARCHAR(64)",
    "lease_expires_at": "TIMESTAMP WITH TIME ZONE",
    "rate_limit_attempts": "INTEGER NOT NULL DEFAULT 0",
    "rate_limit_started_at": "TIMESTAMP WITH TIME ZONE",
}


@dataclass
class ProjectRecord:
    id: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    image_count: int
    error: str | None
    staging_prompt: str | None
    staging_provider: str | None
    world_provider: str | None
    share_token: str
    share_expires_at: datetime | None
    owner_device_id: str | None
    hfov_deg: float | None
    yaws_json: str | None
    lens: str | None
    panorama_key: str | None
    staged_key: str | None
    world_id: str | None
    world_marble_url: str | None
    world_splat_url: str | None
    world_mesh_url: str | None
    world_thumbnail_url: str | None
    world_operation_id: str | None
    world_preview_url: str | None
    world_preview_id: str | None
    staged_world_provider: str | None
    staged_world_id: str | None
    staged_world_marble_url: str | None
    staged_world_preview_url: str | None
    staged_world_splat_url: str | None
    staged_world_mesh_url: str | None
    staged_world_thumbnail_url: str | None
    staged_world_operation_id: str | None
    staged_world_preview_id: str | None
    staging_notes: str | None
    staging_status: str | None
    staging_error: str | None
    active_job: str | None
    provider_error: str | None
    staged_published: bool
    preview_token: str | None
    preview_expires_at: datetime | None


@dataclass
class JobRecord:
    id: str
    project_id: str
    kind: str
    status: str
    operation_id: str | None
    operation_phase: str | None
    media_asset_id: str | None
    attempt_count: int = 0
    delayed_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    rate_limit_attempts: int = 0
    rate_limit_started_at: datetime | None = None


_engine = None
_Session: sessionmaker[Session] | None = None


def init_db(settings: Settings | None = None) -> None:
    global _engine, _Session, _leader_conn, _leader_flag
    settings = settings or get_settings()
    if _leader_conn is not None:
        _leader_conn.close()
        _leader_conn = None
        _leader_flag = False
    if _engine is not None:
        _engine.dispose()
    connect_args = _database_connect_args(settings.database_url)
    _engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
    Base.metadata.create_all(_engine)
    _ensure_columns(_engine)
    _ensure_job_schema(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False)


def _ensure_columns(engine) -> None:
    existing = {column["name"] for column in inspect(engine).get_columns("projects")}
    with engine.begin() as conn:
        for name, ddl in _EXTRA_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE projects ADD COLUMN {name} {ddl}"))
        missing = conn.execute(text("SELECT id FROM projects WHERE preview_token IS NULL")).fetchall()
        for (project_id,) in missing:
            conn.execute(
                text("UPDATE projects SET preview_token = :token WHERE id = :id"),
                {"token": secrets.token_urlsafe(16), "id": project_id},
            )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS projects_preview_token "
                "ON projects (preview_token)"
            )
        )
        conn.execute(
            text(
                "UPDATE projects SET preview_expires_at = share_expires_at "
                "WHERE preview_expires_at IS NULL"
            )
        )


def _database_connect_args(database_url: str) -> dict:
    """SQLite stays single-threaded. Postgres sessions pin UTC.

    Lease and rate-limit instants are ``timestamptz``. Older ``timestamp``
    columns are still read as UTC, which matches how they were written.
    """
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {"options": "-c timezone=UTC"}


_LEASE_TZ_COLUMNS = ("lease_expires_at", "rate_limit_started_at")


def _ensure_job_schema(engine) -> None:
    """Add job columns and the one-open-job index on databases created before them."""
    tables = set(inspect(engine).get_table_names())
    if "jobs" not in tables:
        return
    existing = {column["name"] for column in inspect(engine).get_columns("jobs")}
    with engine.begin() as conn:
        for name, ddl in _JOB_EXTRA_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {ddl}"))
        if engine.dialect.name == "postgresql":
            _promote_lease_timestamps(conn, existing)
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_open_per_project "
                "ON jobs (project_id) WHERE status IN ('pending', 'running', 'delayed')"
            )
        )


def _promote_lease_timestamps(conn, existing: set[str]) -> None:
    """Turn pre-existing naive lease timestamps into ``timestamptz``.

    Those values were written as UTC. ``AT TIME ZONE 'UTC'`` keeps that instant
    when the column gains a time zone.
    """
    for name in _LEASE_TZ_COLUMNS:
        if name not in existing:
            continue
        data_type = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'jobs' AND column_name = :name"
            ),
            {"name": name},
        ).scalar()
        if str(data_type or "").lower() != "timestamp without time zone":
            continue
        conn.execute(
            text(
                f"ALTER TABLE jobs ALTER COLUMN {name} "
                f"TYPE TIMESTAMP WITH TIME ZONE "
                f"USING {name} AT TIME ZONE 'UTC'"
            )
        )


def dispose_db() -> None:
    global _engine, _Session, _leader_conn, _leader_flag
    if _leader_conn is not None:
        _leader_conn.close()
    _leader_conn = None
    _leader_flag = False
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        init_db()
    assert _Session is not None
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _record(row: ProjectRow) -> ProjectRecord:
    return ProjectRecord(
        id=row.id,
        name=row.name,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        image_count=row.image_count,
        error=row.error,
        staging_prompt=row.staging_prompt,
        staging_provider=row.staging_provider,
        world_provider=row.world_provider,
        share_token=row.share_token,
        share_expires_at=row.share_expires_at,
        owner_device_id=row.owner_device_id,
        hfov_deg=row.hfov_deg,
        yaws_json=row.yaws_json,
        lens=row.lens,
        panorama_key=row.panorama_key,
        staged_key=row.staged_key,
        world_id=row.world_id,
        world_marble_url=row.world_marble_url,
        world_splat_url=row.world_splat_url,
        world_mesh_url=row.world_mesh_url,
        world_thumbnail_url=row.world_thumbnail_url,
        world_operation_id=row.world_operation_id,
        world_preview_url=row.world_preview_url,
        world_preview_id=row.world_preview_id,
        staged_world_provider=row.staged_world_provider,
        staged_world_id=row.staged_world_id,
        staged_world_marble_url=row.staged_world_marble_url,
        staged_world_preview_url=row.staged_world_preview_url,
        staged_world_splat_url=row.staged_world_splat_url,
        staged_world_mesh_url=row.staged_world_mesh_url,
        staged_world_thumbnail_url=row.staged_world_thumbnail_url,
        staged_world_operation_id=row.staged_world_operation_id,
        staged_world_preview_id=row.staged_world_preview_id,
        staging_notes=row.staging_notes,
        staging_status=row.staging_status,
        staging_error=row.staging_error,
        active_job=row.active_job,
        provider_error=row.provider_error,
        staged_published=bool(row.staged_published),
        preview_token=row.preview_token,
        preview_expires_at=row.preview_expires_at,
    )


def _job_record(row: JobRow) -> JobRecord:
    return JobRecord(
        id=row.id,
        project_id=row.project_id,
        kind=row.kind,
        status=row.status,
        operation_id=row.operation_id,
        operation_phase=row.operation_phase,
        media_asset_id=row.media_asset_id,
        attempt_count=row.attempt_count or 0,
        delayed_at=row.delayed_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        rate_limit_attempts=row.rate_limit_attempts or 0,
        rate_limit_started_at=row.rate_limit_started_at,
    )


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_device() -> tuple[str, str]:
    """Return (device id, raw token). Only the hash is stored."""
    now = datetime.now(timezone.utc)
    device_id = str(uuid4())
    token = secrets.token_urlsafe(32)
    row = DeviceRow(id=device_id, token_hash=hash_device_token(token), created_at=now)
    with session_scope() as session:
        session.add(row)
        session.flush()
    return device_id, token


def device_id_for_token(token: str) -> str | None:
    digest = hash_device_token(token)
    with session_scope() as session:
        row = session.scalar(select(DeviceRow).where(DeviceRow.token_hash == digest))
        return row.id if row else None


def share_deadline(now: datetime | None = None) -> datetime | None:
    """When the current share token stops working. None means it does not expire."""
    days = get_settings().share_link_ttl_days
    if days <= 0:
        return None
    base = now or datetime.now(timezone.utc)
    return base + timedelta(days=days)


def _share_expired(when: datetime | None, now: datetime | None = None) -> bool:
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return when <= current


def create_project(name: str, owner_device_id: str | None = None) -> ProjectRecord:
    now = datetime.now(timezone.utc)
    row = ProjectRow(
        id=str(uuid4()),
        name=name.strip(),
        status="created",
        created_at=now,
        updated_at=now,
        image_count=0,
        share_token=secrets.token_urlsafe(16),
        share_expires_at=share_deadline(now),
        owner_device_id=owner_device_id,
        preview_token=secrets.token_urlsafe(16),
        preview_expires_at=share_deadline(now),
        staged_published=0,
    )
    with session_scope() as session:
        session.add(row)
        session.flush()
        return _record(row)


def list_projects(owner_device_id: str | None = None, *, admin: bool = False) -> list[ProjectRecord]:
    with session_scope() as session:
        query = select(ProjectRow).order_by(ProjectRow.created_at.desc())
        if not admin:
            query = query.where(ProjectRow.owner_device_id == owner_device_id)
        rows = session.scalars(query).all()
        return [_record(row) for row in rows]


def rotate_share_token(project_id: str) -> ProjectRecord | None:
    deadline = share_deadline()
    return update_project(
        project_id,
        share_token=secrets.token_urlsafe(16),
        share_expires_at=deadline,
        preview_token=secrets.token_urlsafe(16),
        preview_expires_at=deadline,
    )


_PROJECT_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_PUBLIC_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def get_project(project_id: str) -> ProjectRecord | None:
    if not _PROJECT_ID.fullmatch(project_id or ""):
        return None
    with session_scope() as session:
        row = session.get(ProjectRow, project_id)
        return _record(row) if row else None


def delete_project(project_id: str) -> bool:
    """Remove the project row. Share-token lookup then returns nothing."""
    with session_scope() as session:
        row = session.get(ProjectRow, project_id)
        if row is None:
            return False
        for job in session.scalars(select(JobRow).where(JobRow.project_id == project_id)).all():
            session.delete(job)
        session.delete(row)
        return True


def create_job(project_id: str, kind: str) -> JobRecord:
    now = datetime.now(timezone.utc)
    row = JobRow(
        id=str(uuid4()),
        project_id=project_id,
        kind=kind,
        status="pending",
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    with session_scope() as session:
        session.add(row)
        session.flush()
        return _job_record(row)


def get_job(job_id: str) -> JobRecord | None:
    with session_scope() as session:
        row = session.get(JobRow, job_id)
        return _job_record(row) if row else None


def update_job(job_id: str, **fields: object) -> JobRecord | None:
    with session_scope() as session:
        row = session.get(JobRow, job_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        session.flush()
        return _job_record(row)


def delete_job(job_id: str) -> None:
    with session_scope() as session:
        row = session.get(JobRow, job_id)
        if row is not None:
            session.delete(row)


_JOB_PRIORITY = {"stitch": 1, "stage": 2, "world": 3, "staged_world": 4}


_leader_conn = None
_leader_flag = False


def try_resume_leadership() -> tuple[bool, bool]:
    """Return ``(is_leader, just_acquired)``.

    Postgres holds a session advisory lock on a dedicated connection so only
    one replica resumes crashed jobs. SQLite is always the leader.
    """
    global _leader_conn, _leader_flag
    if _engine is None or _engine.dialect.name != "postgresql":
        return True, False
    if _leader_flag and _leader_conn is not None:
        return True, False
    if _leader_conn is None:
        _leader_conn = _engine.connect()
    got = _leader_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _RESUME_LOCK_KEY}).scalar()
    _leader_conn.commit()
    if not got:
        return False, False
    _leader_flag = True
    return True, True


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lease_is_available(row: JobRow, owner: str, now: datetime) -> bool:
    """True when ``owner`` may take the row: nobody holds it, or it expired."""
    if row.lease_expires_at is None:
        return True
    if _as_utc(row.lease_expires_at) <= now:
        return True
    return row.lease_owner == owner


def claim_job_lease(job_id: str, owner: str) -> JobRecord | None:
    """Mark one pending or running job as owned by ``owner``.

    The update matches only when the lease is missing, expired, or already
    held by the same owner. Postgres locks the row with ``SKIP LOCKED`` so
    two workers cannot take it.
    """
    now = datetime.now(timezone.utc)
    until = now + timedelta(seconds=get_settings().job_lease_seconds)
    with session_scope() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            locked = session.scalar(
                select(JobRow.id).where(JobRow.id == job_id).with_for_update(skip_locked=True)
            )
            if locked is None:
                return None
        row = session.get(JobRow, job_id)
        if row is None or row.status not in {"pending", "running"}:
            return None
        if not _lease_is_available(row, owner, now):
            return None
        row.status = "running"
        row.lease_owner = owner
        row.lease_expires_at = until
        row.updated_at = now
        session.flush()
        return _job_record(row)


def renew_job_lease(job_id: str, owner: str) -> bool:
    """Extend a lease this owner still holds. False when the job was reclaimed."""
    now = datetime.now(timezone.utc)
    until = now + timedelta(seconds=get_settings().job_lease_seconds)
    with session_scope() as session:
        row = session.get(JobRow, job_id)
        if row is None or row.lease_owner != owner or row.status != "running":
            return False
        row.lease_expires_at = until
        row.updated_at = now
        return True


def release_job_lease(job_id: str, owner: str) -> None:
    """Drop the lease without deleting the job, so a later pass can claim it."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(JobRow, job_id)
        if row is None or row.lease_owner != owner:
            return
        row.lease_owner = None
        row.lease_expires_at = None
        row.updated_at = now


def delete_owned_job(job_id: str, owner: str) -> bool:
    """Delete the job only when this owner still holds the lease."""
    with session_scope() as session:
        row = session.get(JobRow, job_id)
        if row is None or row.lease_owner != owner:
            return False
        session.delete(row)
        return True


def claim_resumable_jobs(*, fresh_running: bool = True, skip_ids: set[str] | None = None) -> list[JobRecord]:
    """Claim unfinished jobs whose lease is missing or expired.

    One job per project wins, preferring the furthest-along kind. On Postgres
    the resume leader takes ``SELECT ... FOR UPDATE SKIP LOCKED`` so another
    replica cannot claim the same rows. ``skip_ids`` are jobs this process is
    already running; their lease is left untouched. ``fresh_running`` is kept
    so callers do not change. The lease, not ``updated_at``, decides staleness.
    """
    del fresh_running
    if _engine is not None and _engine.dialect.name == "postgresql":
        leader, _just = try_resume_leadership()
        if not leader:
            return []
    skip = skip_ids or set()
    now = datetime.now(timezone.utc)
    until = now + timedelta(seconds=get_settings().job_lease_seconds)
    with session_scope() as session:
        query = select(JobRow).where(JobRow.status.in_(("pending", "running")))
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            query = query.with_for_update(skip_locked=True)
        rows = list(session.scalars(query).all())
        free = [row for row in rows if row.id not in skip and _lease_is_available(row, "", now)]
        best: dict[str, JobRow] = {}
        for row in free:
            current = best.get(row.project_id)
            if current is None or _JOB_PRIORITY.get(row.kind, 0) > _JOB_PRIORITY.get(current.kind, 0):
                best[row.project_id] = row
        claimed: list[JobRow] = []
        for row in free:
            winner = best[row.project_id]
            if row.id != winner.id:
                session.delete(row)
                continue
            row.status = "running"
            row.lease_owner = WORKER_ID
            row.lease_expires_at = until
            row.updated_at = now
            claimed.append(row)
        return [_job_record(row) for row in claimed]


def resumable_project_ids() -> set[str]:
    with session_scope() as session:
        rows = session.scalars(
            select(JobRow.project_id).where(JobRow.status.in_(("pending", "running", "delayed")))
        ).all()
        return set(rows)


def list_delayed_world_jobs() -> list[JobRecord]:
    """World jobs waiting for World Labs credits. They are not failed."""
    with session_scope() as session:
        rows = session.scalars(
            select(JobRow).where(
                JobRow.status == "delayed",
                JobRow.kind.in_(("world", "staged_world")),
            )
        ).all()
        return [_job_record(row) for row in rows]


def get_by_share_token(token: str) -> ProjectRecord | None:
    if not _PUBLIC_TOKEN.fullmatch(token or ""):
        return None
    with session_scope() as session:
        row = session.scalar(select(ProjectRow).where(ProjectRow.share_token == token))
        if row is None or _share_expired(row.share_expires_at):
            return None
        return _record(row)


def get_by_preview_token(token: str) -> ProjectRecord | None:
    """Owner preview. It expires on its own deadline, which rotates with the share link."""
    if not _PUBLIC_TOKEN.fullmatch(token or ""):
        return None
    with session_scope() as session:
        row = session.scalar(select(ProjectRow).where(ProjectRow.preview_token == token))
        if row is None:
            return None
        deadline = row.preview_expires_at if row.preview_expires_at is not None else row.share_expires_at
        if _share_expired(deadline):
            return None
        return _record(row)


def daily_quota_available(device_id: str | None, kind: str, limit: int, needed: int = 1) -> bool:
    if not device_id or limit <= 0:
        return True
    day = datetime.now(timezone.utc).date().isoformat()
    with session_scope() as session:
        row = session.get(DailyUsageRow, (device_id, day, kind))
        used = 0 if row is None else row.count
        return used + max(1, needed) <= limit


_BUMP_QUOTA = text(
    """
    INSERT INTO daily_usage (device_id, day, kind, count)
    SELECT :device_id, :day, :kind, :amount
    WHERE :amount <= :limit
    ON CONFLICT (device_id, day, kind)
    DO UPDATE SET count = daily_usage.count + :amount
    WHERE daily_usage.count + :amount <= :limit
    RETURNING count
    """
)


class _EnqueueStop(Exception):
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome


_BUMP_RATE_WINDOW = text(
    """
    INSERT INTO rate_windows (bucket, window_start, count)
    VALUES (:bucket, :window_start, 1)
    ON CONFLICT (bucket, window_start)
    DO UPDATE SET count = rate_windows.count + 1
    WHERE rate_windows.count < :limit
    RETURNING count
    """
)


def bump_rate_window(
    session: Session,
    bucket: str,
    limit: int,
    window_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Count one hit in a fixed window. False when this window is already full.

    The row lives in the database, so every replica shares the same count.
    """
    if limit <= 0:
        return True
    window_seconds = max(1, int(window_seconds))
    current = now or datetime.now(timezone.utc)
    window_start = int(current.timestamp()) // window_seconds
    row = session.execute(
        _BUMP_RATE_WINDOW,
        {"bucket": bucket[:128], "window_start": window_start, "limit": limit},
    ).first()
    session.execute(
        text("DELETE FROM rate_windows WHERE window_start < :cutoff"),
        {"cutoff": window_start - 2},
    )
    return row is not None


def allow_shared_rate(bucket: str, limit: int, window_seconds: int) -> bool:
    with session_scope() as session:
        return bump_rate_window(session, bucket, limit, window_seconds)


def bump_quota(session: Session, device_id: str, kind: str, limit: int, amount: int = 1) -> bool:
    """Atomically add ``amount``. False when that would pass ``limit``."""
    if limit <= 0:
        return True
    amount = max(1, int(amount))
    row = session.execute(
        _BUMP_QUOTA,
        {
            "device_id": device_id,
            "day": datetime.now(timezone.utc).date().isoformat(),
            "kind": kind,
            "limit": limit,
            "amount": amount,
        },
    ).first()
    return row is not None


def consume_daily_quota(device_id: str | None, kind: str, limit: int) -> bool:
    """Count one paid job for this device today. False when the limit is already met."""
    if not device_id or limit <= 0:
        return True
    with session_scope() as session:
        return bump_quota(session, device_id, kind, limit)


_RELEASE_QUOTA = text(
    """
    UPDATE daily_usage
    SET count = CASE WHEN count > :amount THEN count - :amount ELSE 0 END
    WHERE device_id = :device_id AND day = :day AND kind = :kind AND count > 0
    RETURNING count
    """
)


def release_world_quota(session: Session, device_id: str | None, day: str) -> None:
    """Give back one world charge, and the reserved credits, from ``day``."""
    settings = get_settings()
    if not settings.worldlabs_api_key:
        return
    if device_id and settings.worldlabs_daily_limit > 0:
        session.execute(
            _RELEASE_QUOTA, {"device_id": device_id, "day": day, "kind": "worldlabs", "amount": 1}
        )
    if settings.global_daily_worlds_cap > 0:
        session.execute(
            _RELEASE_QUOTA,
            {"device_id": GLOBAL_QUOTA_DEVICE, "day": day, "kind": "worldlabs", "amount": 1},
        )
    if settings.global_daily_credits_cap > 0:
        from services.quota import reserved_world_credits

        session.execute(
            _RELEASE_QUOTA,
            {
                "device_id": GLOBAL_QUOTA_DEVICE,
                "day": day,
                "kind": "wl_credits",
                "amount": reserved_world_credits(),
            },
        )


def charge_world_quota(session: Session, device_id: str | None) -> bool:
    """Device cap, the global world cap, and the global credit cap, in one transaction."""
    settings = get_settings()
    if not settings.worldlabs_api_key:
        return True
    if device_id and settings.worldlabs_daily_limit > 0:
        if not bump_quota(session, device_id, "worldlabs", settings.worldlabs_daily_limit):
            return False
    if settings.global_daily_worlds_cap > 0:
        if not bump_quota(session, GLOBAL_QUOTA_DEVICE, "worldlabs", settings.global_daily_worlds_cap):
            return False
    if settings.global_daily_credits_cap > 0:
        from services.quota import reserved_world_credits

        if not bump_quota(
            session,
            GLOBAL_QUOTA_DEVICE,
            "wl_credits",
            settings.global_daily_credits_cap,
            amount=reserved_world_credits(),
        ):
            return False
    return True


def charge_staging_quota(session: Session, device_id: str | None) -> bool:
    settings = get_settings()
    if not settings.gemini_api_key or settings.gemini_daily_limit <= 0 or not device_id:
        return True
    return bump_quota(session, device_id, "gemini", settings.gemini_daily_limit)


def _aware(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when


def delay_expired(attempt_count: int, delayed_at: datetime | None, now: datetime | None = None) -> bool:
    """True when a delayed 3D job has been waiting too long or retried too often."""
    settings = get_settings()
    if attempt_count > settings.world_delay_max_attempts:
        return True
    if delayed_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current - _aware(delayed_at) >= timedelta(hours=settings.world_delay_max_hours)


def _open_jobs(session: Session, project_id: str) -> list[JobRow]:
    return list(
        session.scalars(
            select(JobRow).where(
                JobRow.project_id == project_id,
                JobRow.status.in_(OPEN_JOB_STATUSES),
            )
        ).all()
    )


def _queue_deletes_in_session(session: Session, world_ids: list[str], error: str) -> None:
    now = datetime.now(timezone.utc)
    detail = (error or "delete failed")[:500]
    for world_id in dict.fromkeys(world_ids):
        row = session.get(PendingWorldDeleteRow, world_id)
        if row is None:
            session.add(
                PendingWorldDeleteRow(
                    world_id=world_id,
                    attempts=0,
                    last_error=detail,
                    next_attempt_at=now,
                    created_at=now,
                )
            )


_ORIGINAL_WORLD_FIELDS = (
    "world_provider",
    "world_id",
    "world_marble_url",
    "world_splat_url",
    "world_mesh_url",
    "world_thumbnail_url",
    "world_operation_id",
    "world_preview_url",
    "world_preview_id",
)
_STAGED_WORLD_FIELDS = (
    "staged_world_provider",
    "staged_world_id",
    "staged_world_marble_url",
    "staged_world_preview_url",
    "staged_world_splat_url",
    "staged_world_mesh_url",
    "staged_world_thumbnail_url",
    "staged_world_operation_id",
    "staged_world_preview_id",
)


def _world_ids(row: ProjectRow, which: str) -> list[str]:
    from services.world_urls import valid_world_id

    raw = []
    if which in {"original", "all"}:
        raw.extend([row.world_id, row.world_preview_id])
    if which in {"staged", "all"}:
        raw.extend([row.staged_world_id, row.staged_world_preview_id])
    return [item for item in (valid_world_id(value) for value in raw) if item]


def begin_job(
    project_id: str,
    kind: str,
    *,
    project_fields: dict | None = None,
    replace_delayed: bool = False,
    retire_worlds: str | None = None,
    charge: bool = True,
) -> tuple[str, JobRecord | None, list[str]]:
    """Mark the project busy and insert its job in one transaction.

    The third value is the Marble world ids retired by this enqueue. The caller
    deletes those worlds after the transaction commits.

    The update matches only when the project has no open job. A unique partial
    index on ``jobs`` is the backstop. World and staging quota are counted here,
    before the provider is called. ``replace_delayed`` drops a delayed job so a
    re-upload can proceed.
    """
    try:
        with session_scope() as session:
            return _begin_job(
                session,
                project_id,
                kind,
                project_fields or {},
                replace_delayed,
                retire_worlds,
                charge,
            )
    except _EnqueueStop as exc:
        return exc.outcome, None, []
    except IntegrityError:
        return "busy", None, []


def _begin_job(
    session: Session,
    project_id: str,
    kind: str,
    project_fields: dict,
    replace_delayed: bool,
    retire_worlds: str | None,
    charge: bool,
) -> tuple[str, JobRecord, list[str]]:
    dialect = session.get_bind().dialect.name
    query = select(ProjectRow).where(ProjectRow.id == project_id)
    if dialect == "postgresql":
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None:
        raise _EnqueueStop("missing")
    open_jobs = _open_jobs(session, project_id)
    if open_jobs:
        if not (replace_delayed and row.status == "delayed"):
            raise _EnqueueStop("busy")
        for job in open_jobs:
            session.delete(job)
        session.flush()
    if charge and kind in {"world", "staged_world"} and not charge_world_quota(session, row.owner_device_id):
        raise _EnqueueStop("quota")
    if charge and kind == "stage" and not charge_staging_quota(session, row.owner_device_id):
        raise _EnqueueStop("quota")
    now = datetime.now(timezone.utc)
    open_exists = (
        select(JobRow.id)
        .where(JobRow.project_id == project_id, JobRow.status.in_(OPEN_JOB_STATUSES))
        .exists()
    )
    marked = session.execute(
        update(ProjectRow)
        .where(ProjectRow.id == project_id, ~open_exists)
        .values(active_job=kind, updated_at=now)
    )
    if marked.rowcount != 1:
        raise _EnqueueStop("busy")
    session.expire(row)
    row = session.get(ProjectRow, project_id)
    if row is None:
        raise _EnqueueStop("missing")
    retired: list[str] = []
    if retire_worlds:
        retired = _world_ids(row, retire_worlds)
        _queue_deletes_in_session(session, retired, "replaced by a new capture")
        clearing = _ORIGINAL_WORLD_FIELDS if retire_worlds == "original" else ()
        if retire_worlds == "staged":
            clearing = _STAGED_WORLD_FIELDS
        if retire_worlds == "all":
            clearing = _ORIGINAL_WORLD_FIELDS + _STAGED_WORLD_FIELDS
        for field in clearing:
            setattr(row, field, None)
    for key, value in project_fields.items():
        setattr(row, key, value)
    row.active_job = kind
    row.updated_at = now
    if kind == "stitch":
        row.status = project_fields.get("status", "stitching")
    elif kind in {"world", "staged_world"}:
        row.status = "generating_3d"
        row.error = None
        row.provider_error = None
    elif kind == "stage":
        row.staging_status = "staging"
        row.staging_error = None
    job = JobRow(
        id=str(uuid4()),
        project_id=project_id,
        kind=kind,
        status="pending",
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    return "queued", _job_record(job), retired


def claim_delayed_job(job_id: str) -> JobRecord | None:
    """Move one delayed job to running. Only the transaction that updates it wins."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            locked = session.scalar(
                select(JobRow.id)
                .where(JobRow.id == job_id, JobRow.status == "delayed")
                .with_for_update(skip_locked=True)
            )
            if locked is None:
                return None
        result = session.execute(
            update(JobRow)
            .where(JobRow.id == job_id, JobRow.status == "delayed")
            .values(status="running", updated_at=now)
        )
        if result.rowcount != 1:
            return None
        row = session.get(JobRow, job_id)
        return _job_record(row) if row else None


def _quota_day(when: datetime | None) -> str:
    current = _aware(when) if when is not None else datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).date().isoformat()


def _unpaid_world_job(job: JobRow, project: ProjectRow) -> bool:
    """True when this delayed job never got an accepted worlds:generate call."""
    if job.kind not in {"world", "staged_world"}:
        return False
    if job.operation_id or job.operation_phase in {"draft", "full"}:
        return False
    if job.kind == "staged_world":
        started = project.staged_world_operation_id or project.staged_world_id or project.staged_world_preview_id
    else:
        started = project.world_operation_id or project.world_id or project.world_preview_id
    return not started


def cancel_delayed_project(project_id: str) -> str | None:
    """Drop a delayed 3D job and leave the panorama in place. None if it was not delayed.

    A job that was delayed before World Labs accepted a generation returns the
    quota unit charged at enqueue. A job that already has an operation does not.
    """
    with session_scope() as session:
        row = session.get(ProjectRow, project_id)
        if row is None or row.status != "delayed":
            return None
        refunded = False
        for job in _open_jobs(session, project_id):
            if not refunded and _unpaid_world_job(job, row):
                release_world_quota(session, row.owner_device_id, _quota_day(job.created_at))
                refunded = True
            session.delete(job)
        row.status = "stitched" if row.panorama_key else "created"
        row.active_job = None
        row.error = None
        row.provider_error = None
        row.updated_at = datetime.now(timezone.utc)
        return row.status


def touch_job(job_id: str) -> None:
    """Refresh ``updated_at`` while a World Labs poll is making progress.

    Ownership is the lease, not this timestamp. A stale ``updated_at`` does not
    let another worker claim the job.
    """
    with session_scope() as session:
        session.execute(
            update(JobRow).where(JobRow.id == job_id).values(updated_at=datetime.now(timezone.utc))
        )


def queue_world_deletes(world_ids: list[str], error: str) -> None:
    """Remember Marble worlds whose delete did not succeed so a later pass can retry."""
    now = datetime.now(timezone.utc)
    detail = (error or "delete failed")[:500]
    with session_scope() as session:
        for world_id in dict.fromkeys(item for item in world_ids if item):
            row = session.get(PendingWorldDeleteRow, world_id)
            if row is None:
                session.add(
                    PendingWorldDeleteRow(
                        world_id=world_id,
                        attempts=1,
                        last_error=detail,
                        next_attempt_at=now + timedelta(seconds=30),
                        created_at=now,
                    )
                )
                continue
            row.attempts += 1
            row.last_error = detail
            delay = min(30 * (2 ** min(row.attempts, 8)), 6 * 3600)
            row.next_attempt_at = now + timedelta(seconds=delay)


def pending_world_delete_ids(*, force: bool = False) -> list[str]:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        query = select(PendingWorldDeleteRow)
        if not force:
            query = query.where(PendingWorldDeleteRow.next_attempt_at <= now)
        rows = session.scalars(query).all()
        return [row.world_id for row in rows]


def forget_world_delete(world_id: str) -> None:
    with session_scope() as session:
        row = session.get(PendingWorldDeleteRow, world_id)
        if row is not None:
            session.delete(row)


def remember_draft_cleanup(operation_id: str) -> None:
    """Keep a draft operation id so a later free poll can delete the world it creates."""
    from services.world_urls import valid_world_id

    checked = valid_world_id(operation_id)
    if checked is None:
        return
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(PendingDraftCleanupRow, checked)
        if row is None:
            session.add(
                PendingDraftCleanupRow(
                    operation_id=checked,
                    attempts=0,
                    next_attempt_at=now,
                    created_at=now,
                )
            )


def due_draft_cleanups(*, force: bool = False) -> list[tuple[str, int]]:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        query = select(PendingDraftCleanupRow)
        if not force:
            query = query.where(PendingDraftCleanupRow.next_attempt_at <= now)
        rows = session.scalars(query).all()
        return [(row.operation_id, row.attempts) for row in rows]


def pending_draft_cleanup_ids() -> list[str]:
    with session_scope() as session:
        rows = session.scalars(select(PendingDraftCleanupRow)).all()
        return [row.operation_id for row in rows]


def note_draft_cleanup_attempt(operation_id: str) -> None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(PendingDraftCleanupRow, operation_id)
        if row is None:
            return
        row.attempts += 1
        delay = min(30 * (2 ** min(row.attempts, 8)), 6 * 3600)
        row.next_attempt_at = now + timedelta(seconds=delay)


def forget_draft_cleanup(operation_id: str) -> None:
    with session_scope() as session:
        row = session.get(PendingDraftCleanupRow, operation_id)
        if row is not None:
            session.delete(row)


def update_project(project_id: str, **fields: object) -> ProjectRecord | None:
    with session_scope() as session:
        row = session.get(ProjectRow, project_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        session.flush()
        return _record(row)


def fail_interrupted_jobs() -> int:
    """Fail leftover work that has no durable job row to resume.

    Jobs recorded in the jobs table are resumed instead. A project left in a
    transient status by an older deploy, with no job row, is still marked failed
    so the app can retry it.
    """
    active = resumable_project_ids()
    with session_scope() as session:
        rows = session.scalars(
            select(ProjectRow).where(
                or_(
                    ProjectRow.status.in_(TRANSIENT_STATUSES),
                    ProjectRow.staging_status == "staging",
                )
            )
        ).all()
        now = datetime.now(timezone.utc)
        failed = 0
        for row in rows:
            if row.id in active:
                continue
            failed += 1
            if row.status in TRANSIENT_STATUSES:
                previous = row.status
                row.status = "failed"
                if not row.active_job:
                    row.active_job = {
                        "stitching": "stitch",
                        "staging": "stage",
                        "generating_3d": "world",
                        "delayed": "world",
                    }.get(previous, "stitch")
                row.error = "Processing was interrupted by a server restart. Retry from the app."
            if row.staging_status == "staging":
                row.staging_status = "failed"
                row.staging_error = "Staging was interrupted by a server restart. Try staging again."
            row.updated_at = now
        return failed
