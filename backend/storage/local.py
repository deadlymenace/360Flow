from pathlib import Path

STORAGE_ROOT = Path("./storage")
UPLOADS_DIR = STORAGE_ROOT / "uploads"
PANORAMAS_DIR = STORAGE_ROOT / "panoramas"


def ensure_dirs():
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    PANORAMAS_DIR.mkdir(parents=True, exist_ok=True)


def get_project_upload_dir(project_id: str) -> Path:
    d = UPLOADS_DIR / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(project_id: str, filename: str, data: bytes) -> Path:
    d = get_project_upload_dir(project_id)
    path = d / filename
    path.write_bytes(data)
    return path


def get_panorama_path(project_id: str) -> Path:
    return PANORAMAS_DIR / f"{project_id}.jpg"


def get_upload_images(project_id: str) -> list[Path]:
    d = UPLOADS_DIR / project_id
    if not d.exists():
        return []
    return sorted(d.glob("*.jpg")) + sorted(d.glob("*.jpeg")) + sorted(d.glob("*.png"))
