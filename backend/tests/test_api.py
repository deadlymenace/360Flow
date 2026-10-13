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
    assert body["auth"] == "device"
    assert body["admin"] is False


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


def test_device_token_and_admin_key(client, monkeypatch):
    assert client.raw.get("/api/v1/projects").status_code == 401
    monkeypatch.setenv("API_KEY", "secret-key")
    assert client.get("/health").json()["admin"] is True
    assert client.raw.get("/api/v1/projects", headers={"X-API-Key": "wrong-key"}).status_code == 401
    allowed = client.raw.get("/api/v1/projects", headers={"X-API-Key": "secret-key"})
    assert allowed.status_code == 200
    created = client.post("/api/v1/projects", json={"name": "Locked"}).json()
    # The share token page stays public so a listing link does not need the device token.
    assert client.raw.get(f"/v/{created['share_token']}").status_code == 404
    assert client.raw.get(f"/api/v1/projects/{created['id']}", headers={"X-API-Key": "secret-key"}).status_code == 200


def test_delete_project_removes_files_row_and_share_link(client):
    created = client.post("/api/v1/projects", json={"name": "To Delete"}).json()
    project_id = created["id"]
    token = created["share_token"]
    other = client.post("/api/v1/projects", json={"name": "Keep"}).json()
    files = [("images", (f"{index:02d}.jpg", _jpeg(index), "image/jpeg")) for index in range(2)]
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/upload",
        files=files,
        data={"metadata": json.dumps({"hfov_deg": 90, "yaws_deg": [0, 180]})},
    )
    assert uploaded.status_code == 200, uploaded.text

    from db import update_project
    from storage import get_storage, panorama_key, staged_key, upload_key

    update_project(project_id, world_id="world-full", world_preview_id="world-draft", staged_world_id="world-staged")
    storage = get_storage()
    storage.put_bytes(staged_key(project_id), b"staged-bytes", "image/jpeg")
    storage.put_bytes(f"worlds/{project_id}/mesh.glb", b"mesh-bytes", "model/gltf-binary")
    storage.put_bytes(upload_key(other["id"], 0), b"keep-me", "image/jpeg")
    assert storage.exists(panorama_key(project_id))
    assert storage.list_keys(f"uploads/{project_id}/")

    deleted = client.delete(f"/api/v1/projects/{project_id}")
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 404
    assert client.get(f"/v/{token}").status_code == 404
    assert client.get(f"/v/{token}/world").status_code == 404
    assert client.get(f"/v/{token}/panorama.jpg").status_code == 404
    listed = client.get("/api/v1/projects").json()
    assert all(item["id"] != project_id for item in listed)
    assert any(item["id"] == other["id"] for item in listed)
    assert not storage.exists(panorama_key(project_id))
    assert not storage.exists(staged_key(project_id))
    assert storage.list_keys(f"uploads/{project_id}/") == []
    assert storage.list_keys(f"worlds/{project_id}/") == []
    assert storage.exists(upload_key(other["id"], 0))


def test_delete_missing_project_is_404(client):
    missing = client.delete("/api/v1/projects/does-not-exist")
    assert missing.status_code == 404


def test_delete_notifies_world_labs_for_every_stored_world(client, monkeypatch):
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    deleted_urls: list[str] = []

    class _Response:
        status_code = 200

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def delete(self, url, headers=None):
            assert headers["WLT-Api-Key"] == "wl-test"
            deleted_urls.append(url)
            return _Response()

    monkeypatch.setattr("services.world_gen.httpx.AsyncClient", _Client)
    created = client.post("/api/v1/projects", json={"name": "Locked room"}).json()
    from db import update_project

    update_project(
        created["id"],
        world_id="world-full",
        world_preview_id="world-draft",
        staged_world_id="world-staged",
        staged_world_preview_id="world-staged-draft",
    )
    assert client.raw.delete(f"/api/v1/projects/{created['id']}").status_code == 401
    removed = client.delete(f"/api/v1/projects/{created['id']}")
    assert removed.status_code == 204
    assert client.raw.get(f"/v/{created['share_token']}").status_code == 404
    assert sorted(deleted_urls) == sorted(
        [
            "https://api.worldlabs.ai/marble/v1/worlds/world-full",
            "https://api.worldlabs.ai/marble/v1/worlds/world-draft",
            "https://api.worldlabs.ai/marble/v1/worlds/world-staged",
            "https://api.worldlabs.ai/marble/v1/worlds/world-staged-draft",
        ]
    )


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
    assert body["error"] == "Staging failed. Try again."
    assert "test-key" not in body["error"]
    assert "API key not valid" not in body["error"]
    assert "provider_error" not in body
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["provider_error"] is None
    monkeypatch.setenv("API_KEY", "secret-key")
    admin = client.raw.get(
        f"/api/v1/projects/{project_id}",
        headers={"X-API-Key": "secret-key"},
    ).json()
    assert admin["provider_error"] == "HTTP 401: API key not valid"
    assert "test-key" not in admin["provider_error"]
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["status"] == "complete"
    assert project["has_original"] is True
    assert project["world_provider"] == "worldlabs_mock"
    assert project["error"] is None
    assert project["staging_status"] == "failed"


def _device(client):
    token = client.raw.post("/api/v1/devices").json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_paid_world_autostart_respects_the_daily_quota(client, monkeypatch):
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    monkeypatch.setenv("WORLDLABS_JOBS_PER_DEVICE_PER_DAY", "1")
    calls = {"n": 0}

    async def fake_generate(*args, **kwargs):
        calls["n"] += 1
        return {
            "provider": "worldlabs",
            "world_id": "world-1",
            "marble_url": "https://marble.worldlabs.ai/world/world-1",
            "splat_url": None,
            "mesh_url": None,
            "thumbnail_url": None,
            "operation_id": "op-1",
        }

    monkeypatch.setattr("services.pipeline.generate_world", fake_generate)
    headers = _device(client)

    def upload(name: str) -> str:
        created = client.raw.post("/api/v1/projects", headers=headers, json={"name": name}).json()
        files = [("images", (f"{index:02d}.jpg", _jpeg(index), "image/jpeg")) for index in range(2)]
        response = client.raw.post(
            f"/api/v1/projects/{created['id']}/upload",
            headers=headers,
            files=files,
            data={"metadata": json.dumps({"hfov_deg": 90, "yaws_deg": [0, 180]})},
        )
        assert response.status_code == 200, response.text
        return created["id"]

    first = upload("Quota one")
    assert client.raw.get(f"/api/v1/projects/{first}", headers=headers).json()["status"] == "complete"
    second = upload("Quota two")
    body = client.raw.get(f"/api/v1/projects/{second}", headers=headers).json()
    assert body["status"] == "stitched"
    assert body["has_original"] is True
    assert body["error"] == "Daily 3D limit reached. Try again tomorrow."
    assert calls["n"] == 1
    again = client.raw.post(f"/api/v1/projects/{second}/generate-3d", headers=headers)
    assert again.status_code == 429


def test_paid_staging_respects_the_daily_quota(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    monkeypatch.setenv("GEMINI_JOBS_PER_DEVICE_PER_DAY", "1")

    async def fake_stage(image_bytes, prompt):
        return image_bytes, "gemini", None

    monkeypatch.setattr("services.pipeline.stage_image", fake_stage)
    headers = _device(client)
    created = client.raw.post("/api/v1/projects", headers=headers, json={"name": "Stage quota"}).json()
    files = [("images", (f"{index:02d}.jpg", _jpeg(index), "image/jpeg")) for index in range(2)]
    uploaded = client.raw.post(
        f"/api/v1/projects/{created['id']}/upload",
        headers=headers,
        files=files,
        data={"metadata": json.dumps({"hfov_deg": 90, "yaws_deg": [0, 180]})},
    )
    assert uploaded.status_code == 200, uploaded.text
    first = client.raw.post(
        f"/api/v1/projects/{created['id']}/stage",
        headers=headers,
        json={"prompt": "a sofa"},
    )
    assert first.status_code == 200, first.text
    assert client.raw.get(f"/api/v1/projects/{created['id']}/stage/status", headers=headers).json()["status"] == "staged"
    second = client.raw.post(
        f"/api/v1/projects/{created['id']}/stage",
        headers=headers,
        json={"prompt": "a lamp"},
    )
    assert second.status_code == 429
    assert second.json()["detail"] == "Daily AI staging limit reached. Try again tomorrow."
    project = client.raw.get(f"/api/v1/projects/{created['id']}", headers=headers).json()
    assert project["status"] == "complete"
    assert project["has_original"] is True


def test_gemini_quota_does_not_fail_the_project(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    class _Response:
        status_code = 429

        def json(self):
            return {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "You exceeded your current quota, please check your plan and billing details.",
                }
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr("services.staging.httpx.AsyncClient", _Client)
    created = client.post("/api/v1/projects", json={"name": "Quota"}).json()
    project_id = created["id"]
    files = [("images", (f"{index:02d}.jpg", _jpeg(index), "image/jpeg")) for index in range(2)]
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/upload",
        files=files,
        data={"metadata": json.dumps({"hfov_deg": 90, "yaws_deg": [0, 180]})},
    )
    assert uploaded.status_code == 200, uploaded.text
    staged = client.post(f"/api/v1/projects/{project_id}/stage", json={"prompt": "a sofa"})
    assert staged.status_code == 200
    body = client.get(f"/api/v1/projects/{project_id}/stage/status").json()
    assert body["status"] == "failed"
    assert body["error"] == "AI staging is unavailable right now (provider quota)"
    assert "exceeded" not in body["error"]
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["status"] == "complete"
    assert project["has_original"] is True
    assert project["has_staged"] is False
    world = client.get(f"/api/v1/projects/{project_id}/generate-3d/status").json()
    assert world["status"] == "complete"
    assert world["assets"]["viewer_url"].endswith("/world")


def test_share_page_shows_draft_world_while_full_world_generates(client):
    from db import update_project

    created = client.post("/api/v1/projects", json={"name": "Draft room"}).json()
    update_project(
        created["id"],
        status="generating_3d",
        active_job="world",
        panorama_key=f"panoramas/{created['id']}.jpg",
        world_preview_url="https://marble.worldlabs.ai/world/draft-1",
        world_preview_id="draft-1",
    )
    page = client.get(f"/v/{created['share_token']}/world")
    assert page.status_code == 200
    assert "https://marble.worldlabs.ai/world/draft-1" in page.text
    assert "Fast 3D preview" in page.text
    assert "still generating" in page.text
