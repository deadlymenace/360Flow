"""Environment-driven settings.

Postgres, R2, and the external APIs are optional. With nothing set, the API
uses a SQLite file, local disk, and the Gemini / World Labs mocks.
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
    enable_docs: bool
    rate_limit_per_minute: int
    device_registrations_per_hour: int
    trusted_proxy_count: int
    gemini_daily_limit: int
    worldlabs_daily_limit: int
    global_daily_worlds_cap: int
    world_delay_max_hours: int
    world_delay_max_attempts: int
    job_claim_stale_seconds: int
    share_link_ttl_days: int
    embed_frame_ancestors: list[str]
    max_image_pixels: int
    gemini_api_key: str | None
    gemini_model: str
    gemini_image_size: str
    worldlabs_api_key: str | None
    worldlabs_model: str
    worldlabs_draft_model: str | None
    worldlabs_public: bool
    worldlabs_low_credit_threshold: int
    worldlabs_concurrency: int
    worldlabs_retry_attempts: int
    worldlabs_retry_base_seconds: float
    worldlabs_retry_max_seconds: float
    worldlabs_rate_limit_max_attempts: int
    worldlabs_rate_limit_max_seconds: int
    job_lease_seconds: int
    job_lease_heartbeat_seconds: int
    worker_concurrency: int
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
        return "gemini" if self.gemini_api_key else "mock"

    @property
    def world_mode(self) -> str:
        return "worldlabs" if self.worldlabs_api_key else "mock"

    @property
    def auth_mode(self) -> str:
        return "device"


def get_settings() -> Settings:
    storage_dir = Path(os.environ.get("STORAGE_DIR", "./storage")).expanduser().resolve()
    database_url = _blank("DATABASE_URL")
    if database_url:
        database_url = normalize_database_url(database_url)
    else:
        storage_dir.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{storage_dir / '360flow.db'}"

    origins = [
        item.strip()
        for item in os.environ.get("CORS_ORIGINS", "").split(",")
        if item.strip() and item.strip() != "*"
    ]

    image_size = (_blank("GEMINI_IMAGE_SIZE") or "1K").upper()
    if image_size not in {"1K", "2K", "4K"}:
        image_size = "1K"
    draft = os.environ.get("WORLDLABS_DRAFT_MODEL")
    if draft is None:
        draft_model = "marble-1.0-draft"
    elif draft.strip().lower() in {"", "off", "none", "false"}:
        draft_model = None
    else:
        draft_model = draft.strip()

    ancestors = []
    for item in os.environ.get("EMBED_FRAME_ANCESTORS", "").replace(",", " ").split():
        if item in {"*", "'self'"} or item.startswith("https://") or item.startswith("http://"):
            ancestors.append(item)

    job_lease_seconds = min(600, max(30, _int("JOB_LEASE_SECONDS", 90)))
    job_lease_heartbeat_seconds = min(
        max(5, job_lease_seconds // 3),
        max(5, _int("JOB_LEASE_HEARTBEAT_SECONDS", 30)),
    )

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
        enable_docs=_bool("ENABLE_DOCS", False),
        rate_limit_per_minute=max(1, _int("RATE_LIMIT_PER_MINUTE", 120)),
        device_registrations_per_hour=max(1, _int("DEVICE_REGISTRATIONS_PER_HOUR", 30)),
        trusted_proxy_count=max(0, _int("TRUSTED_PROXY_COUNT", 1)),
        gemini_daily_limit=max(0, _int("GEMINI_JOBS_PER_DEVICE_PER_DAY", 20)),
        worldlabs_daily_limit=max(0, _int("WORLDLABS_JOBS_PER_DEVICE_PER_DAY", 10)),
        global_daily_worlds_cap=max(0, _int("GLOBAL_DAILY_WORLDS_CAP", 100)),
        world_delay_max_hours=max(1, _int("WORLD_DELAY_MAX_HOURS", 24)),
        world_delay_max_attempts=max(1, _int("WORLD_DELAY_MAX_ATTEMPTS", 48)),
        job_claim_stale_seconds=max(30, _int("JOB_CLAIM_STALE_SECONDS", 120)),
        share_link_ttl_days=max(0, _int("SHARE_LINK_TTL_DAYS", 30)),
        embed_frame_ancestors=ancestors,
        max_image_pixels=max(1, _int("MAX_IMAGE_PIXELS", 24_000_000)),
        gemini_api_key=_blank("GEMINI_API_KEY"),
        gemini_model=_blank("GEMINI_MODEL") or "gemini-3.1-flash-image",
        gemini_image_size=image_size,
        worldlabs_api_key=_blank("WORLDLABS_API_KEY"),
        worldlabs_model=_blank("WORLDLABS_MODEL") or "marble-1.1",
        worldlabs_draft_model=draft_model,
        worldlabs_public=_bool("WORLDLABS_PUBLIC", False),
        worldlabs_low_credit_threshold=max(0, _int("WORLDLABS_LOW_CREDIT_THRESHOLD", 6000)),
        worldlabs_concurrency=max(1, _int("WORLDLABS_CONCURRENCY", 2)),
        worldlabs_retry_attempts=min(8, max(1, _int("WORLDLABS_RETRY_ATTEMPTS", 5))),
        worldlabs_retry_base_seconds=min(60.0, max(0.0, _float("WORLDLABS_RETRY_BASE_SECONDS", 0.5))),
        worldlabs_retry_max_seconds=min(60.0, max(0.0, _float("WORLDLABS_RETRY_MAX_SECONDS", 32))),
        worldlabs_rate_limit_max_attempts=min(40, max(1, _int("WORLDLABS_RATE_LIMIT_MAX_ATTEMPTS", 20))),
        worldlabs_rate_limit_max_seconds=min(3600, max(30, _int("WORLDLABS_RATE_LIMIT_MAX_SECONDS", 900))),
        job_lease_seconds=job_lease_seconds,
        job_lease_heartbeat_seconds=job_lease_heartbeat_seconds,
        worker_concurrency=max(1, _int("WORKER_CONCURRENCY", 3)),
        stitch_concurrency=max(1, _int("STITCH_CONCURRENCY", 1)),
        jobs_inline=_bool("JOBS_INLINE", False),
        mock_stage_delay=_float("MOCK_STAGE_DELAY", 3),
        mock_world_delay=_float("MOCK_WORLD_DELAY", 5),
        max_image_bytes=_int("MAX_IMAGE_BYTES", 12 * 1024 * 1024),
        max_images=_int("MAX_IMAGES", 24),
        min_images=_int("MIN_IMAGES", 2),
        log_level=_blank("LOG_LEVEL") or "INFO",
    )
