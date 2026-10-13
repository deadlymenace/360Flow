"""Isolated SQLite, local storage, inline jobs, and mocks for the API tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="360flow-test-"))
os.environ["STORAGE_DIR"] = str(_root / "storage")
os.environ["DATABASE_URL"] = f"sqlite:///{_root / 'test.db'}"
os.environ["JOBS_INLINE"] = "1"
os.environ["MOCK_STAGE_DELAY"] = "0"
os.environ["MOCK_WORLD_DELAY"] = "0"
os.environ["API_KEY"] = ""
os.environ["NANOBANANA_API_KEY"] = ""
os.environ["WORLDLABS_API_KEY"] = ""
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["S3_ENDPOINT_URL"] = ""
os.environ["S3_BUCKET"] = ""
os.environ["S3_ACCESS_KEY_ID"] = ""
os.environ["S3_SECRET_ACCESS_KEY"] = ""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    from main import app

    with TestClient(app) as test_client:
        yield test_client
