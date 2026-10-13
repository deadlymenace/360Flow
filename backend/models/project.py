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
    has_original: bool = False
    has_staged: bool = False
    staging_provider: Optional[str] = None
    world_provider: Optional[str] = None
    world_viewer_url: Optional[str] = None
    world_marble_url: Optional[str] = None
    world_thumbnail_url: Optional[str] = None
    world_splat_url: Optional[str] = None
    world_mesh_url: Optional[str] = None
    disclosure: Optional[str] = None


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class StageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


class UploadMetadata(BaseModel):
    hfov_deg: Optional[float] = None
    yaws_deg: Optional[list[float]] = None
    lens: Optional[str] = Field(default=None, max_length=64)
