"""Owner scoping, upload limits, CORS, and disabled API docs."""

from __future__ import annotations

import io
import struct

import pytest

from fastapi import HTTPException
from PIL import Image

from services.stitcher import _hfov_from_exif
from services.uploads import header_dimensions, normalize_upload
from services.world_gen import build_world_request, world_display_name
from viewer import render_pano_page


def _jpeg_claiming(width: int, height: int) -> bytes:
    body = struct.pack(">BHHB", 8, height, width, 1) + bytes((1, 0x11, 0))
    return b"\xff\xd8\xff\xc0" + struct.pack(">H", 2 + len(body)) + body + b"\xff\xd9"


def test_header_dimensions_reject_a_decompression_bomb():
    claimed = _jpeg_claiming(20000, 20000)
    width, height = header_dimensions(claimed)
    assert (width, height) == (20000, 20000)
    try:
        normalize_upload(claimed)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Image dimensions are too large"
    else:
        raise AssertionError("huge image was accepted")


def test_exif_focal_length_survives_normalization(tmp_path):
    """Phone JPEGs keep focal length in the Exif IFD (0x8769), not IFD0."""
    image = Image.new("RGB", (80, 60), (20, 40, 60))
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 15 Pro"
    ifd = exif.get_ifd(0x8769)
    ifd[0x920A] = (222, 100)  # FocalLength 2.22mm
    ifd[0xA405] = 13  # FocalLengthIn35mmFilm, ultra-wide
    raw = io.BytesIO()
    image.save(raw, format="JPEG", exif=exif)
    normalized = normalize_upload(raw.getvalue())
    path = tmp_path / "kept.jpg"
    path.write_bytes(normalized)
    with Image.open(path) as handle:
        root = handle.getexif()
        assert root.get(0xA405) in {None, 0}
        kept = root.get_ifd(0x8769)
        assert int(kept[0xA405]) == 13
        focal = kept[0x920A]
        focal_mm = focal[0] / focal[1] if isinstance(focal, tuple) else float(focal)
        assert focal_mm == pytest.approx(2.22, abs=0.01)
    hfov = _hfov_from_exif(path)
    assert hfov is not None
    assert hfov == pytest.approx(106.17, abs=0.2)


def test_projects_are_scoped_to_the_owning_device(client):
    other = client.raw.post("/api/v1/devices").json()["token"]
    other_headers = {"Authorization": f"Bearer {other}"}
    created = client.post("/api/v1/projects", json={"name": "Private room"}).json()
    project_id = created["id"]
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 200
    assert client.raw.get(f"/api/v1/projects/{project_id}", headers=other_headers).status_code == 404
    listed = client.raw.get("/api/v1/projects", headers=other_headers).json()
    assert listed == []
    upload = client.raw.post(
        f"/api/v1/projects/{project_id}/upload",
        headers=other_headers,
        files=[("images", ("a.jpg", _jpeg_claiming(64, 48), "image/jpeg"))],
    )
    assert upload.status_code == 404
    assert client.raw.post(
        f"/api/v1/projects/{project_id}/stage",
        headers=other_headers,
        json={"prompt": "a sofa"},
    ).status_code == 404
    assert client.raw.post(
        f"/api/v1/projects/{project_id}/generate-3d",
        headers=other_headers,
    ).status_code == 404
    assert client.raw.delete(f"/api/v1/projects/{project_id}", headers=other_headers).status_code == 404
    assert client.get("/api/v1/projects").json()[0]["id"] == project_id or any(
        item["id"] == project_id for item in client.get("/api/v1/projects").json()
    )


def test_share_token_can_be_rotated(client):
    created = client.post("/api/v1/projects", json={"name": "Rotate me"}).json()
    rotated = client.post(f"/api/v1/projects/{created['id']}/share/rotate").json()
    assert rotated["share_token"] != created["share_token"]
    assert rotated["preview_url"] != created["preview_url"]
    assert created["preview_url"]
    from db import get_by_preview_token, get_by_share_token

    old_preview = created["preview_url"].rstrip("/").rsplit("/", 1)[-1]
    new_preview = rotated["preview_url"].rstrip("/").rsplit("/", 1)[-1]
    assert get_by_share_token(created["share_token"]) is None
    assert get_by_share_token(rotated["share_token"]).id == created["id"]
    assert get_by_preview_token(old_preview) is None
    assert get_by_preview_token(new_preview).id == created["id"]


def test_cors_is_closed_unless_an_origin_is_configured(client, monkeypatch):
    blocked = client.raw.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in blocked.headers
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example")
    allowed = client.raw.get("/health", headers={"Origin": "https://app.example"})
    assert allowed.headers.get("access-control-allow-origin") == "https://app.example"
    still_blocked = client.raw.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in still_blocked.headers


def test_docs_are_disabled(client):
    assert client.raw.get("/docs").status_code == 404
    assert client.raw.get("/redoc").status_code == 404
    assert client.raw.get("/openapi.json").status_code == 404


def test_share_pages_send_security_headers(client, monkeypatch):
    response = client.raw.get("/v/missing-token")
    preview_missing = client.raw.get("/p/missing-token")
    assert preview_missing.headers["x-robots-tag"] == "noindex"
    assert "frame-ancestors 'none'" in preview_missing.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex"
    assert response.headers["strict-transport-security"].startswith("max-age=")
    policy = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy
    assert "default-src 'none'" in policy
    embedded_without_allowlist = client.raw.get("/v/missing-token?embed=1")
    assert "frame-ancestors 'none'" in embedded_without_allowlist.headers["content-security-policy"]
    assert "frame-ancestors *" not in embedded_without_allowlist.headers["content-security-policy"]
    monkeypatch.setenv("EMBED_FRAME_ANCESTORS", "https://listings.example")
    embedded = client.raw.get("/v/missing-token?embed=1")
    embedded_policy = embedded.headers["content-security-policy"]
    assert "frame-ancestors https://listings.example" in embedded_policy
    assert "frame-ancestors 'none'" not in embedded_policy


def test_project_name_is_escaped_and_not_substituted(client):
    name = '<script>alert(1)</script> __CONTROLS__'
    created = client.post("/api/v1/projects", json={"name": name}).json()
    from db import update_project

    update_project(created["id"], panorama_key="panoramas/unused.jpg", status="stitched")
    page = render_pano_page(
        title=name,
        asset_base=f"/v/{created['share_token']}",
        has_staged=False,
        has_world=True,
        initial="original",
        embed=False,
    )
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert page.count("Open 3D walkthrough") == 1
    assert "__CONTROLS__" in page


def test_worlds_are_private_and_named_with_the_project_id():
    from config import get_settings

    assert get_settings().worldlabs_public is False
    name = world_display_name("project-123", staged=True)
    body = build_world_request(name, "asset-1", "marble-1.1", public=False)
    assert body["display_name"] == "project-123-staged"
    assert body["permission"]["public"] is False


def test_declared_content_length_is_rejected(client):
    created = client.post("/api/v1/projects", json={"name": "Huge"}).json()
    response = client.post(
        f"/api/v1/projects/{created['id']}/upload",
        files=[("images", ("a.jpg", b"\xff\xd8\xff" + b"0" * 32, "image/jpeg"))],
        headers={"Content-Length": str(400 * 1024 * 1024)},
    )
    assert response.status_code == 413


def test_python_dotenv_is_patched():
    from importlib.metadata import version

    major, minor, patch = (int(part) for part in version("python-dotenv").split(".")[:3])
    assert (major, minor, patch) >= (1, 2, 2)


def test_upload_stream_stops_before_the_rest_of_the_body():
    import asyncio

    from httpguard import capping_receive

    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        return {"type": "http.request", "body": b"a" * 100, "more_body": True}

    wrapped = capping_receive(receive, 150)

    async def run():
        await wrapped()
        await wrapped()
        await wrapped()

    with pytest.raises(HTTPException) as caught:
        asyncio.run(run())
    assert caught.value.status_code == 413
    assert calls == 2


def test_client_ip_uses_the_rightmost_trusted_hop():
    from httpguard import client_ip

    forwarded = "1.1.1.1, 2.2.2.2, 3.3.3.3"
    assert client_ip("10.0.0.8", forwarded, 1) == "3.3.3.3"
    assert client_ip("10.0.0.8", forwarded, 2) == "2.2.2.2"
    assert client_ip("10.0.0.8", forwarded, 0) == "10.0.0.8"
    assert client_ip("10.0.0.8", "1.1.1.1", 2) == "10.0.0.8"


def test_failed_auth_is_limited_by_client_ip(client, monkeypatch):
    from ratelimit import limiter

    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
    limiter.reset()
    first = {"Authorization": "Bearer nope-1", "X-Forwarded-For": "9.9.9.9, 8.8.8.8"}
    second = {"Authorization": "Bearer nope-2", "X-Forwarded-For": "1.2.3.4, 8.8.8.8"}
    other_ip = {"Authorization": "Bearer nope-3", "X-Forwarded-For": "9.9.9.9, 7.7.7.7"}
    assert client.raw.get("/api/v1/projects", headers=first).status_code == 401
    assert client.raw.get("/api/v1/projects", headers=second).status_code == 401
    blocked = client.raw.get("/api/v1/projects", headers={"Authorization": "Bearer nope-4", "X-Forwarded-For": "5.5.5.5, 8.8.8.8"})
    assert blocked.status_code == 429
    assert client.raw.get("/api/v1/projects", headers=other_ip).status_code == 401


def test_rate_limiter_prunes_expired_keys():
    from ratelimit import RateLimiter

    limiter = RateLimiter()
    limiter._hits["old"] = [0.0]
    limiter._max_window = 60
    limiter._last_prune = 0.0
    assert limiter.allow("new", 10, 60, now=1000.0) is True
    assert "old" not in limiter._hits
    assert "new" in limiter._hits


def test_validation_errors_do_not_echo_input(client):
    marker = "ECHO-ME-" + ("x" * 130)
    response = client.post("/api/v1/projects", json={"name": marker})
    assert response.status_code == 422
    assert marker not in response.text
    assert "ECHO-ME" not in response.text
    assert response.json() == {"detail": "Invalid request"}


def test_share_links_expire_and_rotate_refreshes_them(client):
    from datetime import datetime, timedelta, timezone

    from db import update_project

    created = client.post("/api/v1/projects", json={"name": "Expires"}).json()
    expires = datetime.fromisoformat(created["share_expires_at"])
    assert expires > datetime.now(timezone.utc) + timedelta(days=29)
    update_project(
        created["id"],
        panorama_key=f"panoramas/{created['id']}.jpg",
        status="stitched",
        share_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    assert client.get(f"/v/{created['share_token']}").status_code == 404
    rotated = client.post(f"/api/v1/projects/{created['id']}/share/rotate").json()
    assert rotated["share_token"] != created["share_token"]
    refreshed = datetime.fromisoformat(rotated["share_expires_at"])
    assert refreshed > datetime.now(timezone.utc) + timedelta(days=29)
    assert client.get(f"/v/{rotated['share_token']}").status_code == 200
    assert client.get(f"/v/{created['share_token']}").status_code == 404


def test_share_404s_match_for_invalid_expired_and_revoked_tokens(client):
    from datetime import datetime, timedelta, timezone

    from db import update_project
    from storage import get_storage, panorama_key

    expired_project = client.post("/api/v1/projects", json={"name": "Expired link"}).json()
    revoked_project = client.post("/api/v1/projects", json={"name": "Revoked link"}).json()
    for project in (expired_project, revoked_project):
        key = panorama_key(project["id"])
        get_storage().put_bytes(key, _real_jpeg(), "image/jpeg")
        update_project(project["id"], panorama_key=key, status="stitched")
        assert client.get(f"/v/{project['share_token']}").status_code == 200

    update_project(
        expired_project["id"],
        share_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    rotated = client.post(f"/api/v1/projects/{revoked_project['id']}/share/rotate").json()
    assert client.get(f"/v/{rotated['share_token']}").status_code == 200

    def snap(token: str, suffix: str = ""):
        response = client.raw.get(f"/v/{token}{suffix}")
        return (
            response.status_code,
            response.content,
            response.headers.get("content-type"),
            response.headers.get("x-robots-tag"),
            response.headers.get("content-security-policy"),
        )

    unknown = "not-a-real-share-token"
    expired = expired_project["share_token"]
    revoked = revoked_project["share_token"]
    for suffix in ("", "/world", "/panorama.jpg", "/staged.jpg"):
        missed = snap(unknown, suffix)
        assert missed == snap(expired, suffix) == snap(revoked, suffix)
        assert missed[0] == 404
        assert b"expired" not in missed[1].lower()
        assert b"revoked" not in missed[1].lower()
        assert b"not ready" not in missed[1].lower()


def test_failed_world_deletes_are_retried(client, monkeypatch):
    from db import pending_world_delete_ids, update_project
    from services.world_gen import delete_worlds, retry_pending_world_deletes

    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    calls = {"n": 0}

    class _Response:
        def __init__(self, status_code):
            self.status_code = status_code

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def delete(self, url, headers=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Response(500)
            return _Response(200)

    monkeypatch.setattr("services.world_gen.httpx.AsyncClient", _Client)
    created = client.post("/api/v1/projects", json={"name": "Retry world"}).json()
    update_project(created["id"], world_id="world-stuck")

    import asyncio

    asyncio.run(delete_worlds(["world-stuck"]))
    assert "world-stuck" in pending_world_delete_ids(force=True)
    # The retry pass skips worlds a project still references, so drop the reference.
    update_project(created["id"], world_id=None)
    cleared = asyncio.run(retry_pending_world_deletes(force=True))
    assert cleared == 1
    assert pending_world_delete_ids(force=True) == []
    assert calls["n"] == 2


def test_marble_urls_and_world_ids_are_constrained():
    from services.world_gen import parse_world
    from services.world_urls import valid_marble_url, valid_world_id

    assert valid_marble_url("https://marble.worldlabs.ai/world/world-1")
    assert valid_marble_url("http://marble.worldlabs.ai/world/world-1") is None
    assert valid_marble_url("https://evil.example/world/world-1") is None
    assert valid_marble_url("javascript:alert(1)") is None
    assert valid_world_id("world-1") == "world-1"
    assert valid_world_id("../secret") is None
    assert valid_world_id("world/1") is None
    parsed = parse_world(
        {
            "response": {
                "world_id": "../secret",
                "world_marble_url": "https://evil.example/world",
            }
        }
    )
    assert parsed["world_id"] is None
    assert parsed["marble_url"] is None


def test_daily_quota_increment_is_atomic():
    import threading

    from db import consume_daily_quota

    results: list[bool] = []
    barrier = threading.Barrier(8)

    def hit() -> None:
        barrier.wait()
        results.append(consume_daily_quota("quota-race-device", "worldlabs", 1))

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1


def test_global_daily_world_cap_is_shared(monkeypatch):
    from sqlalchemy import delete

    from db import DailyUsageRow, GLOBAL_QUOTA_DEVICE, session_scope
    from services.quota import consume_world_quota

    with session_scope() as session:
        session.execute(delete(DailyUsageRow).where(DailyUsageRow.device_id == GLOBAL_QUOTA_DEVICE))
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    monkeypatch.setenv("WORLDLABS_JOBS_PER_DEVICE_PER_DAY", "10")
    monkeypatch.setenv("GLOBAL_DAILY_WORLDS_CAP", "2")
    assert consume_world_quota("global-cap-a") is True
    assert consume_world_quota("global-cap-b") is True
    assert consume_world_quota("global-cap-c") is False
    assert consume_world_quota("global-cap-a") is False


def test_one_active_job_per_project(client):
    import threading

    from db import begin_job, update_project
    from storage import get_storage, panorama_key

    created = client.post("/api/v1/projects", json={"name": "One job"}).json()
    project_id = created["id"]
    key = panorama_key(project_id)
    get_storage_bytes = b"panorama"
    get_storage().put_bytes(key, get_storage_bytes, "image/jpeg")
    update_project(project_id, status="stitched", panorama_key=key, active_job=None)
    results: list[str] = []
    barrier = threading.Barrier(2)

    def go() -> None:
        barrier.wait()
        results.append(begin_job(project_id, "world")[0])

    threads = [threading.Thread(target=go) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["busy", "queued"]


def test_concurrent_generate_3d_starts_one_world(client, monkeypatch):
    import asyncio

    import httpx

    from db import update_project
    from main import app
    from storage import get_storage, panorama_key

    created = client.post("/api/v1/projects", json={"name": "Double world"}).json()
    project_id = created["id"]
    key = panorama_key(project_id)
    get_storage().put_bytes(key, b"panorama", "image/jpeg")
    update_project(project_id, status="stitched", panorama_key=key, active_job=None)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = {"n": 0}

    async def fake_generate(*args, **kwargs):
        calls["n"] += 1
        started.set()
        await release.wait()
        return {
            "provider": "worldlabs",
            "world_id": "world-once",
            "marble_url": "https://marble.worldlabs.ai/world/world-once",
            "splat_url": None,
            "mesh_url": None,
            "thumbnail_url": None,
            "operation_id": "op-once",
        }

    monkeypatch.setattr("services.pipeline.generate_world", fake_generate)

    async def race():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            headers = {"Authorization": f"Bearer {client.token}"}
            url = f"/api/v1/projects/{project_id}/generate-3d"
            first_task = asyncio.create_task(http.post(url, headers=headers))
            await asyncio.wait_for(started.wait(), timeout=5)
            second = await http.post(url, headers=headers)
            release.set()
            first = await first_task
            return first, second

    first, second = asyncio.run(race())
    assert calls["n"] == 1
    assert sorted([first.status_code, second.status_code]) == [200, 409]


def test_reupload_queues_old_world_deletes_before_clearing_ids(client):
    from db import pending_world_delete_ids, update_project

    created = client.post("/api/v1/projects", json={"name": "Replace"}).json()
    project_id = created["id"]
    update_project(
        project_id,
        status="delayed",
        active_job="world",
        world_id="world-old",
        world_preview_id="draft-old",
        panorama_key=f"panoramas/{project_id}.jpg",
    )
    from db import create_job, update_job

    job = create_job(project_id, "world")
    update_job(job.id, status="delayed")
    files = [
        ("images", (f"{index}.jpg", _real_jpeg(), "image/jpeg"))
        for index in range(2)
    ]
    uploaded = client.post(f"/api/v1/projects/{project_id}/upload", files=files)
    assert uploaded.status_code == 200, uploaded.text
    queued = pending_world_delete_ids(force=True)
    assert "world-old" in queued
    assert "draft-old" in queued
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["world_marble_url"] is None


def test_staging_failure_without_a_provider_message_is_failed(client, monkeypatch):
    from db import update_project
    from services.staging import StagingProviderError
    from storage import get_storage, panorama_key

    async def boom(*args, **kwargs):
        raise StagingProviderError("", None)

    monkeypatch.setattr("services.pipeline.stage_image", boom)
    created = client.post("/api/v1/projects", json={"name": "Stage blank"}).json()
    project_id = created["id"]
    key = panorama_key(project_id)
    get_storage().put_bytes(key, _real_jpeg(), "image/jpeg")
    update_project(project_id, status="stitched", panorama_key=key, active_job=None)
    staged = client.post(f"/api/v1/projects/{project_id}/stage", json={"prompt": "a sofa"})
    assert staged.status_code == 200, staged.text
    body = client.get(f"/api/v1/projects/{project_id}/stage/status").json()
    assert body["status"] == "failed"
    assert body["error"]


def test_health_admin_key_attempts_are_rate_limited(client, monkeypatch):
    from sqlalchemy import delete

    from db import RateWindowRow, session_scope
    from ratelimit import limiter

    with session_scope() as session:
        session.execute(delete(RateWindowRow))
    limiter.reset()
    monkeypatch.setenv("API_KEY", "secret-key")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    assert client.raw.get("/health", headers={"X-API-Key": "nope"}).status_code == 200
    assert client.raw.get("/health", headers={"X-API-Key": "nope"}).status_code == 200
    blocked = client.raw.get("/health", headers={"X-API-Key": "nope"})
    assert blocked.status_code == 429
    limiter.reset()
    still_blocked = client.raw.get("/health", headers={"X-API-Key": "nope"})
    assert still_blocked.status_code == 429
    public = client.raw.get("/health")
    assert public.status_code == 200
    assert public.json() == {"status": "ok"}
    with session_scope() as session:
        session.execute(delete(RateWindowRow))


def _real_jpeg() -> bytes:
    image = Image.new("RGB", (64, 48), (30, 60, 90))
    raw = io.BytesIO()
    image.save(raw, format="JPEG")
    return raw.getvalue()
