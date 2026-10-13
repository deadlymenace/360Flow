import cv2
import numpy as np

from services.disclosure import DISCLOSURE_TEXT, apply_disclosure_label, build_staging_prompt
from services.staging import QUOTA_MESSAGE, build_gemini_request, gemini_client_message
from services.world_gen import (
    FULL_WORLD_TIMEOUT_S,
    WORLD_CREDITS_MESSAGE,
    WORLD_FAILED_MESSAGE,
    WORLD_PROVIDER_QUOTA_MESSAGE,
    build_world_request,
    generation_models,
    world_client_message,
)


def _has_disclosure_bar(image: np.ndarray) -> bool:
    bar = image[-max(8, image.shape[0] // 28) :, :, :]
    target = np.array([46, 26, 26], dtype=np.float32)
    distance = np.linalg.norm(bar.astype(np.float32) - target, axis=2)
    return float(np.mean(distance < 35)) > 0.45


def test_disclosure_banner_is_visible_and_original_is_not():
    image = np.full((220, 480, 3), 180, dtype=np.uint8)
    assert not _has_disclosure_bar(image)
    labeled = apply_disclosure_label(image)
    assert labeled.shape == image.shape
    assert _has_disclosure_bar(labeled)
    assert DISCLOSURE_TEXT == "Virtually staged (AI)"


def test_prompt_preserves_architecture_and_appends_style():
    prompt = build_staging_prompt("paint the walls red and add a linen sofa")
    assert prompt.index("PRESERVE EXACTLY") < prompt.index("paint the walls red")
    lowered = prompt.lower()
    assert "walls" in lowered
    assert "windows" in lowered
    assert "floors" in lowered
    assert "fixtures" in lowered
    assert "only add" in lowered
    assert "ignore that part" in lowered
    assert "Style and furniture to add: paint the walls red and add a linen sofa" in prompt


def test_perspective_prompt_still_preserves_architecture():
    prompt = build_staging_prompt("a linen sofa", perspective=True)
    assert prompt.index("PRESERVE EXACTLY") < prompt.index("a linen sofa")
    assert "one view of a real room" in prompt


def test_perspective_prompt_places_furniture_without_overlap():
    prompt = build_staging_prompt("a linen sofa", perspective=True, yaw_deg=72)
    lowered = prompt.lower()
    assert "yaw 72" in lowered
    assert "floor plane" in lowered
    assert "vanishing" in lowered
    assert "do not overlap existing furniture" in lowered
    assert "bed" in lowered
    assert "same places" in lowered
    assert prompt.index("PRESERVE EXACTLY") < prompt.index("a linen sofa")


def test_gemini_request_matches_generate_content():
    body = build_gemini_request("add a sofa", b"jpeg-bytes", "1K")
    parts = body["contents"][0]["parts"]
    assert parts[0]["text"] == "add a sofa"
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
    assert parts[1]["inline_data"]["data"]
    config = body["generationConfig"]
    assert config["responseModalities"] == ["TEXT", "IMAGE"]
    assert config["imageConfig"]["aspectRatio"] == "4:3"
    assert config["imageConfig"]["imageSize"] == "1K"
    from config import get_settings

    assert get_settings().gemini_model == "gemini-3.1-flash-image"


def test_gemini_quota_errors_use_a_client_message():
    quota = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}}
    assert gemini_client_message(429, quota) == QUOTA_MESSAGE
    assert gemini_client_message(400, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "busy"}}) == QUOTA_MESSAGE
    assert gemini_client_message(403, {"error": {"message": "Your quota has been exceeded"}}) == QUOTA_MESSAGE
    assert gemini_client_message(401, {"error": {"message": "API key not valid"}}) is None
    assert "quota" in QUOTA_MESSAGE


def test_world_provider_errors_map_credits_and_quota():
    assert world_client_message(402, "") == WORLD_CREDITS_MESSAGE
    assert world_client_message(400, "The account is out of credits") == WORLD_CREDITS_MESSAGE
    assert world_client_message(429, "slow down") == WORLD_PROVIDER_QUOTA_MESSAGE
    assert world_client_message(403, "quota exceeded for this project") == WORLD_PROVIDER_QUOTA_MESSAGE
    assert world_client_message(500, "internal error") == WORLD_FAILED_MESSAGE
    assert "HTTP" not in WORLD_CREDITS_MESSAGE
    assert "HTTP" not in WORLD_PROVIDER_QUOTA_MESSAGE


def test_full_world_poll_allows_at_least_fifteen_minutes():
    assert FULL_WORLD_TIMEOUT_S >= 15 * 60


def test_generation_runs_draft_then_full_model():
    assert generation_models("marble-1.0-draft", "marble-1.1") == ["marble-1.0-draft", "marble-1.1"]
    assert generation_models(None, "marble-1.1") == ["marble-1.1"]
    assert generation_models("marble-1.1", "marble-1.1") == ["marble-1.1"]


def test_world_request_marks_equirect_as_pano():
    body = build_world_request("Living Room", "asset-123", "marble-1.1", public=True)
    assert body["world_prompt"]["type"] == "image"
    assert body["world_prompt"]["is_pano"] is True
    assert body["world_prompt"]["image_prompt"] == {
        "source": "media_asset",
        "media_asset_id": "asset-123",
    }
    assert body["permission"]["public"] is True
    assert body["model"] == "marble-1.1"


def test_decode_roundtrip_of_labeled_jpeg():
    image = np.full((180, 360, 3), 200, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", apply_disclosure_label(image), [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    assert _has_disclosure_bar(decoded)
