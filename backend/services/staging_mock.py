import asyncio


async def mock_stage(panorama_path: str, prompt: str) -> dict:
    """Simulate NanoBanana staging API. Returns after 3s delay."""
    await asyncio.sleep(3)
    return {
        "status": "staged",
        "staged_image_path": panorama_path,
        "prompt_used": prompt,
        "provider": "nanobanana_mock",
    }
