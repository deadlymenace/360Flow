from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    STITCHING = "stitching"
    STITCHED = "stitched"
    STAGING = "staging"
    STAGED = "staged"
    GENERATING_3D = "generating_3d"
    DELAYED = "delayed"
    COMPLETE = "complete"
    FAILED = "failed"


class Project(BaseModel):
    id: str
    name: str
    status: ProjectStatus
    created_at: datetime
    image_count: int = 0
    panorama_path: Optional[str] = None
    error: Optional[str] = None
    staging_prompt: Optional[str] = None
    share_token: str
    share_url: Optional[str] = None
    share_expires_at: Optional[datetime] = None
    preview_url: Optional[str] = None
    has_original: bool = False
    has_staged: bool = False
    staged_published: bool = False
    staging_provider: Optional[str] = None
    world_provider: Optional[str] = None
    world_viewer_url: Optional[str] = None
    # Provider (World Labs / CDN) URLs are never returned: the CDN links are
    # unsigned, non-expiring capability URLs. Clients only get state flags and
    # our own viewer URLs.
    world_ready: bool = False
    world_draft_ready: bool = False
    staged_world_ready: bool = False
    staged_world_draft_ready: bool = False
    staged_world_viewer_url: Optional[str] = None
    staging_notes: Optional[str] = None
    staging_status: Optional[str] = None
    staging_error: Optional[str] = None
    active_job: Optional[str] = None
    disclosure: Optional[str] = None
    provider_error: Optional[str] = None


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class StageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=400)


class UploadMetadata(BaseModel):
    hfov_deg: Optional[float] = None
    yaws_deg: Optional[list[float]] = None
    lens: Optional[str] = Field(default=None, max_length=64)
