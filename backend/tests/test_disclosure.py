import cv2
import numpy as np

from services.disclosure import DISCLOSURE_TEXT, apply_disclosure_label, build_staging_prompt
from services.staging import build_gemini_request
from services.world_gen import build_world_request, generation_models


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

    assert get_settings().gemini_model == "gemini-nano-banana-2.1"


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
