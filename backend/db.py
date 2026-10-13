"""Project persistence. Postgres when DATABASE_URL is set, otherwise SQLite."""

from __future__ import annotations

import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config import Settings, get_settings

TRANSIENT_STATUSES = ("uploading", "stitching", "staging", "generating_3d")


class Base(DeclarativeBase):
    pass


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
    _Session = sessionmaker(bind=_engine, expire_on_commit=False)


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
    )


def create_project(name: str) -> ProjectRecord:
    now = datetime.now(timezone.utc)
    row = ProjectRow(
        id=str(uuid4()),
        name=name.strip(),
        status="created",
        created_at=now,
        updated_at=now,
        image_count=0,
        share_token=secrets.token_urlsafe(16),
    )
    with session_scope() as session:
        session.add(row)
        session.flush()
        return _record(row)


def list_projects() -> list[ProjectRecord]:
    with session_scope() as session:
        rows = session.scalars(select(ProjectRow).order_by(ProjectRow.created_at.desc())).all()
        return [_record(row) for row in rows]


def get_project(project_id: str) -> ProjectRecord | None:
    with session_scope() as session:
        row = session.get(ProjectRow, project_id)
        return _record(row) if row else None


def get_by_share_token(token: str) -> ProjectRecord | None:
    with session_scope() as session:
        row = session.scalar(select(ProjectRow).where(ProjectRow.share_token == token))
        return _record(row) if row else None


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
    """In-process jobs do not survive a restart. Mark them so the app can retry."""
    with session_scope() as session:
        rows = session.scalars(select(ProjectRow).where(ProjectRow.status.in_(TRANSIENT_STATUSES))).all()
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = "failed"
            row.error = "Processing was interrupted by a server restart. Retry from the app."
            row.updated_at = now
        return len(rows)
