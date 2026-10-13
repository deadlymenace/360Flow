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
    image = Image.new("RGB", (80, 60), (20, 40, 60))
    exif = Image.Exif()
    exif[0xA405] = 18
    raw = io.BytesIO()
    image.save(raw, format="JPEG", exif=exif)
    normalized = normalize_upload(raw.getvalue())
    path = tmp_path / "kept.jpg"
    path.write_bytes(normalized)
    hfov = _hfov_from_exif(path)
    assert hfov is not None
    assert hfov == pytest.approx(87.75, abs=0.2)


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
    from db import get_by_share_token

    assert get_by_share_token(created["share_token"]) is None
    assert get_by_share_token(rotated["share_token"]).id == created["id"]


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


def test_share_pages_send_security_headers(client):
    response = client.raw.get("/v/missing-token")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    policy = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy
    assert "default-src 'none'" in policy


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
