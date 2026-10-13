"""Preview stand-in used when WORLDLABS_API_KEY is not set."""

from __future__ import annotations

import asyncio

from config import get_settings


async def mock_generate_3d(panorama_path: str) -> dict:
    await asyncio.sleep(get_settings().mock_world_delay)
    return {
        "status": "complete",
        "world_id": None,
        "marble_url": None,
        "splat_url": "https://mock.worldlabs.ai/assets/fake-splat.spz",
        "mesh_url": "https://mock.worldlabs.ai/assets/fake-mesh.glb",
        "thumbnail_url": "https://mock.worldlabs.ai/assets/fake-thumb.jpg",
        "provider": "worldlabs_mock",
    }
