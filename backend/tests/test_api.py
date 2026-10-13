"""End-to-end API: stitch, mock stage, mock world, share page, and access control."""

from __future__ import annotations

import json

import cv2
import numpy as np

from services.disclosure import DISCLOSURE_TEXT


def _jpeg(index: int, width: int = 96, height: int = 128) -> bytes:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = (40 + index * 20, 90, 170)
    image[30:50, :] = (255, 255, 255)
    rng = np.random.default_rng(index + 5)
    image += rng.integers(0, 25, image.shape, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    return buf.tobytes()


def _has_disclosure_bar(image: np.ndarray) -> bool:
    bar = image[-max(8, image.shape[0] // 28) :, :, :]
    target = np.array([46, 26, 26], dtype=np.float32)
    distance = np.linalg.norm(bar.astype(np.float32) - target, axis=2)
    return float(np.mean(distance < 35)) > 0.45


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["storage"] == "local"
    assert body["database"] == "sqlite"
    assert body["staging"] == "mock"
    assert body["world"] == "mock"
    assert body["auth"] == "open"


def test_project_survives_reopen(client):
    from db import dispose_db, get_project, init_db

    created = client.post("/api/v1/projects", json={"name": "Persisted Room"}).json()
    dispose_db()
    init_db()
    again = get_project(created["id"])
    assert again is not None and again.name == "Persisted Room"
    listed = client.get("/api/v1/projects").json()
    assert any(item["id"] == created["id"] for item in listed)


def test_rejects_bad_uploads(client):
    created = client.post("/api/v1/projects", json={"name": "Bad"}).json()
    url = f"/api/v1/projects/{created['id']}/upload"
    one = client.post(url, files=[("images", ("a.jpg", _jpeg(0), "image/jpeg"))])
    assert one.status_code == 400
    garbage = client.post(
        url,
        files=[
            ("images", ("a.jpg", b"not-an-image", "image/jpeg")),
            ("images", ("b.jpg", b"also-not", "image/jpeg")),
        ],
    )
    assert garbage.status_code == 400
    huge = b"\xff\xd8\xff" + b"0" * (12 * 1024 * 1024 + 8)
    oversized = client.post(
        url,
        files=[
            ("images", ("a.jpg", huge, "image/jpeg")),
            ("images", ("b.jpg", _jpeg(1), "image/jpeg")),
        ],
    )
    assert oversized.status_code == 413


def test_full_pipeline_with_mocks(client):
    created = client.post("/api/v1/projects", json={"name": "Living Room"}).json()
    project_id = created["id"]
    token = created["share_token"]
    yaws = [0, 90, 180, 270]
    files = [("images", (f"{index:02d}.jpg", _jpeg(index), "image/jpeg")) for index in range(4)]
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/upload",
        files=files,
        data={"metadata": json.dumps({"hfov_deg": 100, "yaws_deg": yaws, "lens": "ultra-wide"})},
    )
    assert uploaded.status_code == 200, uploaded.text
    status = client.get(f"/api/v1/projects/{project_id}/status").json()
    assert status["status"] == "complete", status
    assert status["has_original"] is True
    assert status["has_staged"] is False
    assert status["disclosure"] is None
    assert status["world_provider"] == "worldlabs_mock"

    original = client.get(f"/api/v1/projects/{project_id}/panorama")
    assert original.status_code == 200
    original_bytes = original.content
    decoded = cv2.imdecode(np.frombuffer(original_bytes, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[1] == decoded.shape[0] * 2
    assert not _has_disclosure_bar(decoded)
    assert float(decoded[:6].mean()) > 20
    assert float(decoded[-6:].mean()) > 20

    world_status = client.get(f"/api/v1/projects/{project_id}/generate-3d/status").json()
    assert world_status["status"] == "complete", world_status
    assert world_status["provider"] == "worldlabs_mock"
    assert world_status["assets"]["viewer_url"].endswith("/world")
    assert world_status["assets"]["marble_url"] is None
    assert world_status["disclosure"] is None
    assert world_status["staged_assets"] is None

    world_page = client.get(f"/v/{token}/world")
    assert world_page.status_code == 200
    assert 'class="chip hide"' in world_page.text
    assert "navigable 3D world has not been generated" in world_page.text
    assert "Open 3D walkthrough" in client.get(f"/v/{token}").text

    staged = client.post(
        f"/api/v1/projects/{project_id}/stage",
        json={"prompt": "modern linen sofa and oak coffee table"},
    )
    assert staged.status_code == 200, staged.text
    stage_status = client.get(f"/api/v1/projects/{project_id}/stage/status").json()
    assert stage_status["status"] == "staged", stage_status
    assert stage_status["provider"] == "gemini_mock"
    assert stage_status["disclosure"] == DISCLOSURE_TEXT

    untouched = client.get(f"/api/v1/projects/{project_id}/panorama").content
    assert untouched == original_bytes
    labeled = client.get(f"/api/v1/projects/{project_id}/staged")
    labeled_image = cv2.imdecode(np.frombuffer(labeled.content, np.uint8), cv2.IMREAD_COLOR)
    assert _has_disclosure_bar(labeled_image)
    assert labeled.content != original_bytes

    staged_world = client.post(f"/api/v1/projects/{project_id}/generate-3d?source=staged")
    assert staged_world.status_code == 200, staged_world.text
    staged_status = client.get(f"/api/v1/projects/{project_id}/generate-3d/status").json()
    assert staged_status["status"] == "complete", staged_status
    assert staged_status["disclosure"] == DISCLOSURE_TEXT
    assert staged_status["assets"]["viewer_url"].endswith("/world")
    assert staged_status["staged_assets"]["viewer_url"].endswith("/world?source=staged")
    assert staged_status["staged_assets"]["marble_url"] is None

    page = client.get(f"/v/{token}?view=staged")
    assert page.status_code == 200
    assert DISCLOSURE_TEXT in page.text
    assert "Original" in page.text and "Staged" in page.text and "Compare" in page.text
    assert 'class="chip "' in page.text

    original_page = client.get(f"/v/{token}?view=original")
    assert 'class="chip hide"' in original_page.text

    public_staged = client.get(f"/v/{token}/staged.jpg")
    public_image = cv2.imdecode(np.frombuffer(public_staged.content, np.uint8), cv2.IMREAD_COLOR)
    assert _has_disclosure_bar(public_image)
    public_original = cv2.imdecode(
        np.frombuffer(client.get(f"/v/{token}/panorama.jpg").content, np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert not _has_disclosure_bar(public_original)

    original_world = client.get(f"/v/{token}/world")
    assert 'class="chip hide"' in original_world.text
    staged_page = client.get(f"/v/{token}/world?source=staged")
    assert staged_page.status_code == 200
    assert 'class="chip "' in staged_page.text
    assert DISCLOSURE_TEXT in staged_page.text


def test_api_key_protects_api_and_not_the_share_page(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-key")
    assert client.get("/health").json()["auth"] == "api-key"
    assert client.get("/api/v1/projects").status_code == 401
    allowed = client.get("/api/v1/projects", headers={"X-API-Key": "secret-key"})
    assert allowed.status_code == 200
    created = client.post(
        "/api/v1/projects",
        headers={"X-API-Key": "secret-key"},
        json={"name": "Locked"},
    ).json()
    # The share token page stays public so a listing link does not need the key.
    assert client.get(f"/v/{created['share_token']}").status_code == 404


def test_gemini_http_error_fails_the_job(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    class _Response:
        status_code = 401

        def json(self):
            return {"error": {"message": "API key not valid"}}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            assert kwargs["headers"]["x-goog-api-key"] == "test-key"
            return _Response()

    monkeypatch.setattr("services.staging.httpx.AsyncClient", _Client)
    created = client.post("/api/v1/projects", json={"name": "Needs Gemini"}).json()
    project_id = created["id"]
    files = [("images", (f"{index:02d}.jpg", _jpeg(index), "image/jpeg")) for index in range(2)]
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/upload",
        files=files,
        data={"metadata": json.dumps({"hfov_deg": 90, "yaws_deg": [0, 180]})},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert client.get(f"/api/v1/projects/{project_id}/status").json()["status"] == "complete"
    staged = client.post(f"/api/v1/projects/{project_id}/stage", json={"prompt": "a sofa"})
    assert staged.status_code == 200
    body = client.get(f"/api/v1/projects/{project_id}/stage/status").json()
    assert body["status"] == "failed"
    assert "Gemini" in body["error"]
    assert "test-key" not in body["error"]
