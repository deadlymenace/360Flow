"""Project persistence. Postgres when DATABASE_URL is set, otherwise SQLite."""

from __future__ import annotations

import hashlib
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator
from uuid import uuid4

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, inspect, or_, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config import Settings, get_settings

TRANSIENT_STATUSES = ("uploading", "stitching", "staging", "generating_3d")


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


class DailyUsageRow(Base):
    __tablename__ = "daily_usage"

    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class PendingWorldDeleteRow(Base):
    __tablename__ = "pending_world_deletes"

    world_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
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


_engine = None
_Session: sessionmaker[Session] | None = None


def init_db(settings: Settings | None = None) -> None:
    global _engine, _Session
    settings = settings or get_settings()
    if _engine is not None:
        _engine.dispose()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    _engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
    Base.metadata.create_all(_engine)
    _ensure_columns(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False)


def _ensure_columns(engine) -> None:
    existing = {column["name"] for column in inspect(engine).get_columns("projects")}
    with engine.begin() as conn:
        for name, ddl in _EXTRA_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE projects ADD COLUMN {name} {ddl}"))


def dispose_db() -> None:
    global _engine, _Session
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
    return update_project(
        project_id,
        share_token=secrets.token_urlsafe(16),
        share_expires_at=share_deadline(),
    )


def get_project(project_id: str) -> ProjectRecord | None:
    with session_scope() as session:
        row = session.get(ProjectRow, project_id)
        return _record(row) if row else None


def delete_project(project_id: str) -> bool:
    """Remove the project row. Share-token lookup then returns nothing."""
    with session_scope() as session:
        row = session.get(ProjectRow, project_id)
        if row is None:
            return False
        session.delete(row)
        return True


def get_by_share_token(token: str) -> ProjectRecord | None:
    with session_scope() as session:
        row = session.scalar(select(ProjectRow).where(ProjectRow.share_token == token))
        if row is None or _share_expired(row.share_expires_at):
            return None
        return _record(row)


def daily_quota_available(device_id: str | None, kind: str, limit: int) -> bool:
    if not device_id or limit <= 0:
        return True
    day = datetime.now(timezone.utc).date().isoformat()
    with session_scope() as session:
        row = session.get(DailyUsageRow, (device_id, day, kind))
        return row is None or row.count < limit


def consume_daily_quota(device_id: str | None, kind: str, limit: int) -> bool:
    """Count one paid job for this device today. False when the limit is already met."""
    if not device_id or limit <= 0:
        return True
    day = datetime.now(timezone.utc).date().isoformat()
    with session_scope() as session:
        row = session.get(DailyUsageRow, (device_id, day, kind))
        if row is None:
            session.add(DailyUsageRow(device_id=device_id, day=day, kind=kind, count=1))
            return True
        if row.count >= limit:
            return False
        row.count += 1
        return True


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
    """In-process jobs do not survive a restart. Mark them so the app can retry.

    A staging job that was in progress fails on its own status. A finished
    panorama and 3D world stay available.
    """
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
        for row in rows:
            if row.status in TRANSIENT_STATUSES:
                previous = row.status
                row.status = "failed"
                if not row.active_job:
                    row.active_job = {
                        "stitching": "stitch",
                        "staging": "stage",
                        "generating_3d": "world",
                    }.get(previous, "stitch")
                row.error = "Processing was interrupted by a server restart. Retry from the app."
            if row.staging_status == "staging":
                row.staging_status = "failed"
                row.staging_error = "Staging was interrupted by a server restart. Try staging again."
            row.updated_at = now
        return len(rows)
