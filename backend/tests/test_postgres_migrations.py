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

from db import Base, ProjectRow, _EXTRA_COLUMNS, _ensure_columns

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


def test_sqlite_migration_adds_share_expiry_as_timestamp():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(_OLD_PROJECTS))
    _migrate_like_startup(engine)
    _assert_share_expiry_roundtrip(engine)
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
    assert {"devices", "daily_usage", "pending_world_deletes", "jobs"} <= table_names
    engine.dispose()


def _migrate_like_startup(engine) -> None:
    """Same order as ``init_db``: create missing tables, then alter projects."""
    Base.metadata.create_all(engine)
    _ensure_columns(engine)


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


def _postgres_data_type(engine, column: str) -> str:
    with engine.connect() as conn:
        found = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'projects' AND column_name = :column"
            ),
            {"column": column},
        ).scalar()
    assert found, column
    return str(found)
