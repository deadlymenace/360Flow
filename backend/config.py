"""Environment-driven settings.

Postgres, R2, and the external APIs are optional. With nothing set, the API
uses a SQLite file, local disk, and the NanoBanana / World Labs mocks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


def _blank(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


def normalize_database_url(url: str) -> str:
    """Railway's Postgres plugin uses postgres://, which SQLAlchemy will not accept."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def is_public_base_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        return False
    return True


@dataclass(frozen=True)
class Settings:
    database_url: str
    storage_dir: Path
    s3_endpoint_url: str | None
    s3_bucket: str | None
    s3_access_key_id: str | None
    s3_secret_access_key: str | None
    s3_region: str
    api_key: str | None
    public_base_url: str | None
    cors_origins: list[str]
    nanobanana_api_key: str | None
    nanobanana_resolution: str
    worldlabs_api_key: str | None
    worldlabs_model: str
    worldlabs_public: bool
    stitch_concurrency: int
    jobs_inline: bool
    mock_stage_delay: float
    mock_world_delay: float
    max_image_bytes: int
    max_images: int
    min_images: int
    log_level: str

    @property
    def uses_object_storage(self) -> bool:
        return bool(self.s3_bucket and self.s3_access_key_id and self.s3_secret_access_key)

    @property
    def storage_backend_name(self) -> str:
        return "r2" if self.uses_object_storage else "local"

    @property
    def database_backend_name(self) -> str:
        return "sqlite" if self.database_url.startswith("sqlite") else "postgres"

    @property
    def staging_mode(self) -> str:
        return "nanobanana" if self.nanobanana_api_key else "mock"

    @property
    def world_mode(self) -> str:
        return "worldlabs" if self.worldlabs_api_key else "mock"

    @property
    def auth_mode(self) -> str:
        return "api-key" if self.api_key else "open"


def get_settings() -> Settings:
    storage_dir = Path(os.environ.get("STORAGE_DIR", "./storage")).expanduser().resolve()
    database_url = _blank("DATABASE_URL")
    if database_url:
        database_url = normalize_database_url(database_url)
    else:
        storage_dir.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{storage_dir / '360flow.db'}"

    origins = [item.strip() for item in os.environ.get("CORS_ORIGINS", "*").split(",") if item.strip()]
    if not origins:
        origins = ["*"]

    resolution = (_blank("NANOBANANA_RESOLUTION") or "2K").upper()
    if resolution not in {"1K", "2K", "4K"}:
        resolution = "2K"

    return Settings(
        database_url=database_url,
        storage_dir=storage_dir,
        s3_endpoint_url=_blank("S3_ENDPOINT_URL"),
        s3_bucket=_blank("S3_BUCKET"),
        s3_access_key_id=_blank("S3_ACCESS_KEY_ID"),
        s3_secret_access_key=_blank("S3_SECRET_ACCESS_KEY"),
        s3_region=_blank("S3_REGION") or "auto",
        api_key=_blank("API_KEY"),
        public_base_url=_blank("PUBLIC_BASE_URL"),
        cors_origins=origins,
        nanobanana_api_key=_blank("NANOBANANA_API_KEY"),
        nanobanana_resolution=resolution,
        worldlabs_api_key=_blank("WORLDLABS_API_KEY"),
        worldlabs_model=_blank("WORLDLABS_MODEL") or "marble-1.1",
        worldlabs_public=_bool("WORLDLABS_PUBLIC", True),
        stitch_concurrency=max(1, _int("STITCH_CONCURRENCY", 1)),
        jobs_inline=_bool("JOBS_INLINE", False),
        mock_stage_delay=_float("MOCK_STAGE_DELAY", 3),
        mock_world_delay=_float("MOCK_WORLD_DELAY", 5),
        max_image_bytes=_int("MAX_IMAGE_BYTES", 12 * 1024 * 1024),
        max_images=_int("MAX_IMAGES", 24),
        min_images=_int("MIN_IMAGES", 2),
        log_level=_blank("LOG_LEVEL") or "INFO",
    )
