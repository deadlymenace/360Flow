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
os.environ["ENABLE_DOCS"] = ""
os.environ["CORS_ORIGINS"] = ""
os.environ["RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["SHARE_RATE_PER_MINUTE"] = "10000"
os.environ["SHARE_ASSET_RATE_PER_MINUTE"] = "10000"
os.environ["DEVICE_REGISTRATIONS_PER_HOUR"] = "10000"
os.environ["GEMINI_API_KEY"] = ""
os.environ["WORLDLABS_API_KEY"] = ""
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["S3_ENDPOINT_URL"] = ""
os.environ["S3_BUCKET"] = ""
os.environ["S3_ACCESS_KEY_ID"] = ""
os.environ["S3_SECRET_ACCESS_KEY"] = ""

import pytest
from fastapi.testclient import TestClient


class AuthedClient:
    """Test client that sends one device token on every call.

    ``raw`` is the underlying client with no Authorization header.
    """

    def __init__(self, raw: TestClient, token: str):
        self.raw = raw
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    def request(self, method: str, url: str, **kwargs):
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", None) or {})
        return self.raw.request(method, url, headers=headers, **kwargs)

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self.request("DELETE", url, **kwargs)


@pytest.fixture(scope="session")
def client():
    from main import app

    with TestClient(app) as raw:
        registered = raw.post("/api/v1/devices")
        assert registered.status_code == 200, registered.text
        yield AuthedClient(raw, registered.json()["token"])
