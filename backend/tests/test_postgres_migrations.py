"""Column migrations must run on Postgres, not only SQLite.

Railway startup calls ``create_all`` and then ``_ensure_columns``. ``create_all``
does not add columns to a table that already exists, so an older ``projects``
table is altered with the DDL in ``_EXTRA_COLUMNS``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from db import Base, ProjectRow, _EXTRA_COLUMNS, _JOB_EXTRA_COLUMNS, _ensure_columns, _ensure_job_schema

# Shared by SQLite and Postgres. DATETIME is rejected by Postgres.
_SAFE_TYPE = re.compile(r"^(TEXT|INTEGER|BIGINT|REAL|DOUBLE PRECISION|TIMESTAMP|VARCHAR\(\d+\))$")

_OLD_PROJECTS = """
CREATE TABLE projects (
    id VARCHAR(36) PRIMARY KEY,
    name TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    image_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    staging_prompt TEXT,
    staging_provider VARCHAR(32),
    world_provider VARCHAR(32),
    share_token VARCHAR(64) NOT NULL UNIQUE,
    hfov_deg DOUBLE PRECISION,
    yaws_json TEXT,
    lens VARCHAR(64),
    panorama_key TEXT,
    staged_key TEXT,
    world_id VARCHAR(64),
    world_marble_url TEXT,
    world_splat_url TEXT,
    world_mesh_url TEXT,
    world_thumbnail_url TEXT,
    world_operation_id VARCHAR(64)
)
"""


def test_added_column_types_are_valid_on_postgres_and_sqlite():
    for name, ddl in _EXTRA_COLUMNS.items():
        assert _SAFE_TYPE.fullmatch(ddl), f"{name} uses {ddl}, which is not valid on both SQLite and Postgres"
        assert "DATETIME" not in ddl.upper()


def test_job_lease_column_types_are_valid_on_postgres_and_sqlite():
    for name, ddl in _JOB_EXTRA_COLUMNS.items():
        assert "DATETIME" not in ddl.upper(), name
        prefix = ddl.split()[0]
        assert prefix in {"INTEGER", "BIGINT", "REAL", "TEXT", "TIMESTAMP"} or prefix.startswith("VARCHAR("), ddl
        if prefix.startswith("VARCHAR"):
            assert re.fullmatch(r"VARCHAR\(\d+\)", prefix), ddl


def test_sqlite_migration_adds_share_expiry_as_timestamp():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(_OLD_PROJECTS))
    _migrate_like_startup(engine)
    _assert_share_expiry_roundtrip(engine)
    indexes = {index["name"] for index in inspect(engine).get_indexes("projects")}
    assert "projects_preview_token" in indexes
    _assert_rate_window_is_shared(engine)
    engine.dispose()


def test_postgres_migration_adds_every_column():
    url = os.environ.get("TEST_POSTGRES_URL", "").strip()
    if not url:
        if os.environ.get("CI"):
            pytest.fail("TEST_POSTGRES_URL is required in CI")
        pytest.skip("Set TEST_POSTGRES_URL to run migrations against Postgres")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS pending_world_deletes"))
        conn.execute(text("DROP TABLE IF EXISTS daily_usage"))
        conn.execute(text("DROP TABLE IF EXISTS rate_windows"))
        conn.execute(text("DROP TABLE IF EXISTS devices"))
        conn.execute(text("DROP TABLE IF EXISTS jobs"))
        conn.execute(text("DROP TABLE IF EXISTS projects"))
        conn.execute(text(_OLD_PROJECTS))
    _migrate_like_startup(engine)
    columns = {column["name"]: column for column in inspect(engine).get_columns("projects")}
    assert set(_EXTRA_COLUMNS) <= set(columns)
    data_type = _postgres_data_type(engine, "share_expires_at")
    assert data_type.startswith("timestamp"), data_type
    _assert_share_expiry_roundtrip(engine)
    table_names = set(inspect(engine).get_table_names())
    assert {"devices", "daily_usage", "pending_world_deletes", "jobs", "rate_windows"} <= table_names
    index_names = {index["name"] for index in inspect(engine).get_indexes("jobs")}
    assert "jobs_one_open_per_project" in index_names
    job_columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    assert {"lease_owner", "lease_expires_at", "rate_limit_attempts", "rate_limit_started_at"} <= job_columns
    lease_type = _postgres_data_type(engine, "lease_expires_at", table="jobs")
    assert lease_type == "timestamp with time zone", lease_type
    started_type = _postgres_data_type(engine, "rate_limit_started_at", table="jobs")
    assert started_type == "timestamp with time zone", started_type
    project_indexes = {index["name"] for index in inspect(engine).get_indexes("projects")}
    assert "projects_preview_token" in project_indexes
    with engine.begin() as conn:
        locked = conn.execute(text("SELECT pg_try_advisory_lock(360360160)")).scalar()
        assert locked is True
        conn.execute(text("SELECT pg_advisory_unlock(360360160)"))
        conn.execute(
            text(
                "SELECT id FROM jobs WHERE status IN ('pending', 'running') FOR UPDATE SKIP LOCKED"
            )
        )
    _assert_rate_window_is_shared(engine)
    engine.dispose()


def _assert_rate_window_is_shared(engine) -> None:
    """Two sessions see one counter. A process-local limiter would not."""
    from db import bump_rate_window

    with Session(engine) as session:
        assert bump_rate_window(session, "health-share", 1, 60) is True
        session.commit()
    with Session(engine) as session:
        assert bump_rate_window(session, "health-share", 1, 60) is False
        session.commit()


def _migrate_like_startup(engine) -> None:
    """Same order as ``init_db``: create missing tables, then alter projects."""
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    _ensure_job_schema(engine)


def _assert_share_expiry_roundtrip(engine) -> None:
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    with Session(engine) as session:
        session.add(
            ProjectRow(
                id="migrated-room",
                name="Migrated",
                status="created",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                image_count=0,
                share_token="share-tok",
                share_expires_at=expires,
            )
        )
        session.commit()
        loaded = session.get(ProjectRow, "migrated-room")
        assert loaded is not None
        assert loaded.share_expires_at is not None
        stored = loaded.share_expires_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        assert abs((stored - expires).total_seconds()) < 1


def _postgres_data_type(engine, column: str, table: str = "projects") -> str:
    with engine.connect() as conn:
        found = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).scalar()
    assert found, column
    return str(found)


_OLD_JOBS = """
CREATE TABLE jobs (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    operation_id TEXT,
    operation_phase VARCHAR(16),
    media_asset_id TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
)
"""


def test_sqlite_migration_adds_job_lease_columns():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(_OLD_JOBS))
        conn.execute(
            text(
                "INSERT INTO jobs (id, project_id, kind, status, created_at, updated_at) "
                "VALUES ('job-1', 'project-1', 'world', 'running', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    _ensure_job_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    assert {
        "attempt_count",
        "delayed_at",
        "lease_owner",
        "lease_expires_at",
        "rate_limit_attempts",
        "rate_limit_started_at",
    } <= columns
    with engine.connect() as conn:
        attempts = conn.execute(text("SELECT rate_limit_attempts FROM jobs WHERE id = 'job-1'")).scalar()
    assert attempts == 0
    engine.dispose()


def test_postgres_promotes_naive_lease_timestamps():
    """A pre-existing TIMESTAMP lease column becomes timestamptz and keeps the UTC instant."""
    url = os.environ.get("TEST_POSTGRES_URL", "").strip()
    if not url:
        if os.environ.get("CI"):
            pytest.fail("TEST_POSTGRES_URL is required in CI")
        pytest.skip("Set TEST_POSTGRES_URL to run migrations against Postgres")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS jobs"))
        conn.execute(
            text(
                """
                CREATE TABLE jobs (
                    id VARCHAR(36) PRIMARY KEY,
                    project_id VARCHAR(36) NOT NULL,
                    kind VARCHAR(32) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    lease_expires_at TIMESTAMP,
                    rate_limit_started_at TIMESTAMP
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO jobs "
                "(id, project_id, kind, status, created_at, updated_at, lease_expires_at, rate_limit_started_at) "
                "VALUES ('job-tz', 'project-tz', 'world', 'running', "
                "'2026-10-15 12:00:00', '2026-10-15 12:00:00', "
                "'2026-10-15 12:00:00', '2026-10-15 11:00:00')"
            )
        )
    _ensure_job_schema(engine)
    assert _postgres_data_type(engine, "lease_expires_at", table="jobs") == "timestamp with time zone"
    assert _postgres_data_type(engine, "rate_limit_started_at", table="jobs") == "timestamp with time zone"
    with engine.connect() as conn:
        conn.execute(text("SET TIME ZONE 'America/New_York'"))
        lease, started = conn.execute(
            text("SELECT lease_expires_at, rate_limit_started_at FROM jobs WHERE id = 'job-tz'")
        ).one()
        conn.commit()
    assert lease.tzinfo is not None and started.tzinfo is not None
    assert lease.astimezone(timezone.utc) == datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    assert started.astimezone(timezone.utc) == datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
    engine.dispose()
