"""Streaming 3D files, per-project fill lock, refetch tool, share rate bucket, CSP."""

from __future__ import annotations

import asyncio
import re

import pytest
from fastapi.responses import StreamingResponse

import db
from services import world_assets
from storage import get_storage
from tests.test_world_assets import CDN_MESH, CDN_SPLAT, _device, _world_project

FULL = "https://cdn.marble.worldlabs.ai/w1/full.spz"
SMALL = "https://cdn.marble.worldlabs.ai/w1/100k.spz"


@pytest.fixture
def slow_cdn(monkeypatch):
    calls: list[str] = []

    async def fake_download(url: str, limit: int, dest):
        calls.append(url)
        await asyncio.sleep(0.05)
        dest.write_bytes((b"S" if url.endswith(".spz") else b"M") * (3 * 1024 * 1024 + 7))
        return True

    monkeypatch.setattr(world_assets, "_download", fake_download)
    return calls


def test_world_files_stream_in_chunks_with_no_store(client, slow_cdn):
    auth = _device(client)
    created = _world_project(client, auth)
    for path in ("world.spz", "world.glb"):
        res = client.raw.get(f"/api/v1/projects/{created['id']}/{path}", headers=auth)
        assert res.status_code == 200
        assert res.headers["cache-control"] == "no-store"
        assert int(res.headers["content-length"]) == 3 * 1024 * 1024 + 7
        assert len(res.content) == 3 * 1024 * 1024 + 7
    token = created["share_token"]
    share = client.raw.get(f"/v/{token}/world.spz")
    assert share.status_code == 200 and share.headers["cache-control"] == "no-store"


def test_world_asset_response_is_streaming_and_chunked():
    from routers.projects import world_asset_response

    storage = get_storage()
    storage.put_bytes("worlds/stream-test/original.spz", b"x" * (2 * 1024 * 1024 + 1), "application/octet-stream")
    chunks, size = storage.open_stream("worlds/stream-test/original.spz")
    response = world_asset_response((chunks, size), "spz")
    assert isinstance(response, StreamingResponse)
    blocks = list(storage.open_stream("worlds/stream-test/original.spz")[0])
    assert len(blocks) == 3 and max(len(b) for b in blocks) == 1024 * 1024
    storage.delete_prefix("worlds/stream-test/")


def test_concurrent_cache_fills_download_once(client, slow_cdn):
    auth = _device(client)
    created = _world_project(client, auth)
    project = db.get_project(created["id"])

    async def many():
        return await asyncio.gather(*(world_assets.world_asset_stream(project, "original", "spz") for _ in range(5)))

    results = asyncio.run(many())
    assert all(r is not None for r in results)
    assert slow_cdn.count(CDN_SPLAT) == 1


def test_refetch_tool_replaces_with_lowest_res_and_never_generates(client, slow_cdn, monkeypatch):
    from services import world_gen

    async def forbidden(*args, **kwargs):
        raise AssertionError("refetch must never generate a world")

    monkeypatch.setattr(world_gen, "generate_world", forbidden)
    lookups: list[str] = []

    async def fake_lookup(world_id: str):
        lookups.append(world_id)
        return {"world_id": world_id, "assets": {"splats": {"spz_urls": {"full_res": FULL, "100k": SMALL}}}}

    monkeypatch.setattr(world_assets, "_fetch_world_record", fake_lookup)
    auth = _device(client)
    created = _world_project(client, auth)
    db.update_project(created["id"], world_splat_url=FULL)
    key = world_assets.world_asset_key(created["id"], "original", "spz")
    get_storage().put_bytes(key, b"OLD-FULL-RES", "application/octet-stream")

    project = db.get_project(created["id"])
    assert asyncio.run(world_assets.refetch_lowest_splat(project, apply=False)) == "would refetch"
    assert get_storage().get_bytes(key) == b"OLD-FULL-RES"  # dry run touches nothing
    assert slow_cdn == []

    assert asyncio.run(world_assets.refetch_lowest_splat(project, apply=True)) == "refetched"
    assert slow_cdn == [SMALL]
    assert get_storage().get_bytes(key).startswith(b"S")
    assert db.get_project(created["id"]).world_splat_url == SMALL
    again = asyncio.run(world_assets.refetch_lowest_splat(db.get_project(created["id"]), apply=True))
    assert again == "ok: already lowest-res"
    assert asyncio.run(world_assets.refetch_lowest_splat(project, "staged", apply=True)) == "skip: no world"


def test_refetch_script_is_dry_run_by_default(monkeypatch, capsys):
    from scripts import refetch_world_splats as script

    seen = {}

    async def fake_run(ids, apply, sources):
        seen.update(ids=ids, apply=apply, sources=sources)
        return 0

    monkeypatch.setattr(script, "run", fake_run)
    assert script.main(["--all"]) == 0
    assert seen == {"ids": None, "apply": False, "sources": ["original", "staged"]}
    script.main(["--project", "p1", "--apply", "--source", "original"])
    assert seen == {"ids": ["p1"], "apply": True, "sources": ["original"]}


def test_share_pages_have_their_own_rate_bucket(client, monkeypatch):
    from httpguard import share_rate_bucket
    from ratelimit import limiter

    assert share_rate_bucket("/v/t") == "share-page"
    assert share_rate_bucket("/p/t/world") == "share-page"
    assert share_rate_bucket("/v/t/world.spz") == "share-asset"
    assert share_rate_bucket("/viewer-assets/splat-viewer.js") == "share-asset"
    assert share_rate_bucket("/api/v1/projects") is None

    auth = _device(client)
    created = _world_project(client, auth)
    token = created["share_token"]
    limiter.reset()
    try:
        monkeypatch.setenv("SHARE_RATE_PER_MINUTE", "3")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
        for _ in range(3):
            assert client.raw.get(f"/v/{token}").status_code == 200
        assert client.raw.get(f"/v/{token}").status_code == 429
        assert client.raw.get(f"/p/{token}/world").status_code == 429
        # Page bucket exhausted: the API and the page's files still work.
        assert client.raw.get("/api/v1/projects", headers=auth).status_code == 200
        assert client.raw.get(f"/v/{token}/panorama.jpg").status_code != 429
        assert client.raw.get("/viewer-assets/pano-viewer.js").status_code == 200
        limiter.reset()
        # Exhausting the API bucket does not block share pages.
        for _ in range(3):
            client.raw.get("/api/v1/projects", headers=auth)
        assert client.raw.get("/api/v1/projects", headers=auth).status_code == 429
        assert client.raw.get(f"/v/{token}").status_code == 200
    finally:
        limiter.reset()


def test_viewer_csp_has_no_unsafe_inline_or_data_scripts(client):
    auth = _device(client)
    created = _world_project(client, auth)
    token = created["share_token"]
    for path in (f"/v/{token}", f"/v/{token}/world"):
        res = client.raw.get(path)
        assert res.status_code == 200
        csp = res.headers["content-security-policy"]
        script_src = re.search(r"script-src ([^;]+)", csp).group(1).split()
        assert "'unsafe-inline'" not in script_src and "data:" not in script_src
        # Every executable inline script is gone; the import map is allowed by hash.
        for tag in re.findall(r"<script([^>]*)>", res.text):
            assert "src=" in tag or 'type="application/json"' in tag or 'type="importmap"' in tag, tag
        if 'type="importmap"' in res.text:
            from viewer import IMPORTMAP_CSP_HASH

            assert f"'{IMPORTMAP_CSP_HASH}'" in script_src
    for name in ("pano-viewer.js", "splat-viewer.js"):
        js = client.raw.get(f"/viewer-assets/{name}")
        assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")


def test_config_block_cannot_break_out_of_script(client):
    from viewer import render_pano_page

    html = render_pano_page(
        title="x</script><script>alert(1)</script>",
        asset_base="/v/</script><b>",
        has_staged=False,
        has_world=False,
        initial="original",
        embed=False,
    )
    block = re.search(r'<script type="application/json" id="viewer-config">(.*?)</script>', html, re.S).group(1)
    assert "<" not in block and ">" not in block
