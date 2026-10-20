"""Self-hosted world viewer: private splat cache, auth, deletes, no provider URLs."""

from __future__ import annotations

import asyncio

import pytest

import db
from services import world_assets
from storage import get_storage, world_asset_prefix

SPLAT = b"SPZ-FAKE-BYTES" * 10
MESH = b"glTF-FAKE" * 10
CDN_SPLAT = "https://cdn.marble.worldlabs.ai/w1/abc_ceramic.spz"
CDN_MESH = "https://cdn.marble.worldlabs.ai/w1/def.glb"


@pytest.fixture
def fake_cdn(monkeypatch):
    calls: list[str] = []

    async def fake_download(url: str, limit: int, dest):
        calls.append(url)
        dest.write_bytes(SPLAT if url.endswith(".spz") else MESH)
        return True

    monkeypatch.setattr(world_assets, "_download", fake_download)
    return calls


def _device(client):
    token = client.raw.post("/api/v1/devices").json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _world_project(client, auth, name="World room", **extra):
    created = client.raw.post("/api/v1/projects", json={"name": name}, headers=auth).json()
    project_id = created["id"]
    db.update_project(
        project_id,
        status="complete",
        panorama_key=f"panoramas/{project_id}.jpg",
        world_provider="worldlabs",
        world_id=f"w-{project_id[:8]}",
        world_marble_url=f"https://marble.worldlabs.ai/world/w-{project_id[:8]}",
        world_splat_url=CDN_SPLAT,
        world_mesh_url=CDN_MESH,
        world_thumbnail_url="https://cdn.marble.worldlabs.ai/w1/thumb.webp",
        **extra,
    )
    get_storage().put_bytes(f"panoramas/{project_id}.jpg", b"\xff\xd8pano", "image/jpeg")
    return created


def test_api_responses_never_carry_provider_urls(client):
    auth = _device(client)
    created = _world_project(client, auth)
    for url in (
        f"/api/v1/projects/{created['id']}",
        "/api/v1/projects",
        f"/api/v1/projects/{created['id']}/status",
        f"/api/v1/projects/{created['id']}/generate-3d/status",
    ):
        text = client.raw.get(url, headers=auth).text
        assert "worldlabs.ai" not in text, url
    body = client.raw.get(f"/api/v1/projects/{created['id']}", headers=auth).json()
    assert body["world_ready"] is True
    assert body["world_viewer_url"].endswith("/world")


def test_device_auth_splat_route(client, fake_cdn):
    auth = _device(client)
    other = _device(client)
    created = _world_project(client, auth)
    url = f"/api/v1/projects/{created['id']}/world.spz"
    assert client.raw.get(url).status_code == 401
    assert client.raw.get(url, headers=other).status_code == 404
    ok = client.raw.get(url, headers=auth)
    assert ok.status_code == 200
    assert ok.content == SPLAT
    assert ok.headers["cache-control"] == "no-store"
    # Second request is served from the private cache, not the CDN.
    assert client.raw.get(url, headers=auth).content == SPLAT
    assert fake_cdn == [CDN_SPLAT]
    mesh = client.raw.get(f"/api/v1/projects/{created['id']}/world.glb", headers=auth)
    assert mesh.status_code == 200 and mesh.content == MESH


def test_share_route_serves_splat_and_viewer_page(client, fake_cdn):
    auth = _device(client)
    created = _world_project(client, auth, name="Share room")
    token = created["share_token"]
    spz = client.raw.get(f"/v/{token}/world.spz")
    assert spz.status_code == 200 and spz.content == SPLAT
    page = client.raw.get(f"/v/{token}/world")
    assert page.status_code == 200
    assert "/viewer-assets/spark-2.3.1.module.min.js" in page.text
    assert f"/v/{token}/world.spz" in page.text
    assert "Loading 3D tour" in page.text
    assert "worldlabs" not in page.text
    csp = page.headers["content-security-policy"]
    assert "frame-src" not in csp and "worldlabs" not in csp
    # Unpublished staged world is not public.
    assert client.raw.get(f"/v/{token}/world.spz?source=staged").status_code == 404
    assert client.raw.get("/v/not-a-token/world.spz").status_code == 404


def test_viewer_assets_allowlist(client):
    ok = client.raw.get("/viewer-assets/three.module.min.js")
    assert ok.status_code == 200 and "javascript" in ok.headers["content-type"]
    assert client.raw.get("/viewer-assets/spark-2.3.1.module.min.js").status_code == 200
    assert client.raw.get("/viewer-assets/..%2Fviewer.py").status_code == 404
    assert client.raw.get("/viewer-assets/main.py").status_code == 404


def test_only_world_labs_hosts_are_fetched(client, fake_cdn):
    auth = _device(client)
    created = _world_project(client, auth, name="Evil")
    db.update_project(created["id"], world_splat_url="https://evil.example/x.spz")
    assert client.raw.get(f"/api/v1/projects/{created['id']}/world.spz", headers=auth).status_code == 404
    assert fake_cdn == []
    assert world_assets.allowed_asset_url("http://cdn.marble.worldlabs.ai/a.spz") is None
    assert world_assets.allowed_asset_url("https://mock.worldlabs.ai/a.spz") is None
    assert world_assets.allowed_asset_url("https://worldlabs.ai.evil.example/a.spz") is None


def test_project_delete_removes_cached_world_and_queues_world_delete(client, fake_cdn, monkeypatch):
    monkeypatch.setenv("WORLDLABS_API_KEY", "wl-test")
    auth = _device(client)
    created = _world_project(client, auth, name="Delete me")
    project_id = created["id"]
    world_id = db.get_project(project_id).world_id
    assert client.raw.get(f"/api/v1/projects/{project_id}/world.spz", headers=auth).status_code == 200
    assert get_storage().list_keys(world_asset_prefix(project_id))
    assert client.raw.delete(f"/api/v1/projects/{project_id}", headers=auth).status_code == 204
    assert not get_storage().list_keys(world_asset_prefix(project_id))
    assert world_id in db.pending_world_delete_ids(force=True)
    db.forget_world_delete(world_id)  # keep the shared queue clean for other tests
    assert client.raw.get(f"/v/{created['share_token']}/world.spz").status_code == 404


def test_delete_all_my_data_removes_cached_worlds(client, fake_cdn):
    auth = _device(client)
    created = _world_project(client, auth, name="All my data")
    project_id = created["id"]
    asyncio.run(world_assets.cache_world_assets(project_id, "original", CDN_SPLAT, CDN_MESH))
    assert len(get_storage().list_keys(world_asset_prefix(project_id))) == 2
    assert client.raw.delete("/api/v1/devices/me", headers=auth).status_code == 204
    assert not get_storage().list_keys(world_asset_prefix(project_id))


def test_finished_world_is_cached_by_the_pipeline(client, fake_cdn):
    from services import pipeline

    auth = _device(client)
    created = client.raw.post("/api/v1/projects", json={"name": "Pipeline"}, headers=auth).json()
    project_id = created["id"]
    db.update_project(project_id, status="generating_3d", panorama_key=f"panoramas/{project_id}.jpg")
    result = {
        "world_id": "pipe-world",
        "marble_url": "https://marble.worldlabs.ai/world/pipe-world",
        "splat_url": CDN_SPLAT,
        "mesh_url": CDN_MESH,
        "thumbnail_url": None,
        "provider": "worldlabs",
        "operation_id": "op-1",
    }
    monkey_guard = getattr(pipeline, "_guard_write")
    try:
        pipeline._guard_write = lambda project_id: None
        asyncio.run(pipeline._store_world(project_id, result, False, []))
    finally:
        pipeline._guard_write = monkey_guard
    keys = get_storage().list_keys(world_asset_prefix(project_id))
    assert sorted(k.rsplit("/", 1)[-1] for k in keys) == ["original.glb", "original.spz"]


def test_smallest_splat_is_cached_for_phones():
    from services.world_gen import parse_world, smallest_splat_url

    urls = {
        "full_res": "https://cdn.marble.worldlabs.ai/w/full.spz",
        "500k": "https://cdn.marble.worldlabs.ai/w/500k.spz",
        "100k": "https://cdn.marble.worldlabs.ai/w/100k.spz",
    }
    assert smallest_splat_url(urls).endswith("/100k.spz")
    assert smallest_splat_url({"full_res": urls["full_res"]}).endswith("/full.spz")
    assert smallest_splat_url({}) is None
    parsed = parse_world({"response": {"world_id": "w1", "assets": {"splats": {"spz_urls": urls}}}})
    assert parsed["splat_url"].endswith("/100k.spz")


def test_viewer_page_has_fallbacks_and_no_public_urls(client):
    from viewer import render_splat_page

    page = render_splat_page(title="Room", asset_base="/v/tok", staged_source=False, has_staged=False)
    script = client.raw.get("/viewer-assets/splat-viewer.js").text
    assert '<script type="module">' not in page  # no inline module script any more
    html = page + script
    assert "Can't show 3D on this device" in html
    assert "webgl2" in html and "LOAD_TIMEOUT_MS = 30000" in html
    assert "Try again" in html
    assert "antialias: false" in html and "Math.min(window.devicePixelRatio || 1, 2)" in html
    assert "http://" not in html and "https://" not in html
    assert "worldlabs" not in html
