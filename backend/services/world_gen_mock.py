import asyncio


async def mock_generate_3d(panorama_path: str) -> dict:
    """Simulate World Labs 3D generation. Returns after 5s delay."""
    await asyncio.sleep(5)
    return {
        "status": "complete",
        "splat_url": "https://mock.worldlabs.ai/assets/fake-splat.spz",
        "mesh_url": "https://mock.worldlabs.ai/assets/fake-mesh.glb",
        "thumbnail_url": "https://mock.worldlabs.ai/assets/fake-thumb.jpg",
        "provider": "worldlabs_mock",
    }
