from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

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
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    status: ProjectStatus = ProjectStatus.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    image_count: int = 0
    panorama_path: Optional[str] = None
    error: Optional[str] = None
    staging_prompt: Optional[str] = None
    staging_started_at: Optional[datetime] = None
    gen3d_started_at: Optional[datetime] = None


class CreateProjectRequest(BaseModel):
    name: str


class StageRequest(BaseModel):
    prompt: str


# In-memory project store
projects_db: dict[str, Project] = {}
