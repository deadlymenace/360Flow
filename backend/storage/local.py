from pathlib import Path

BASE_DIR = Path("./storage")
UPLOADS_DIR = BASE_DIR / "uploads"
PANORAMA_DIR = BASE_DIR / "panoramas"


def ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    PANORAMA_DIR.mkdir(parents=True, exist_ok=True)


def save_upload(project_id: str, filename: str, data: bytes) -> Path:
    ensure_dirs()
    project_dir = UPLOADS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    file_path = project_dir / filename
    file_path.write_bytes(data)
    return file_path


def get_panorama_path(project_id: str) -> Path:
    ensure_dirs()
    return PANORAMA_DIR / f"{project_id}.jpg"
