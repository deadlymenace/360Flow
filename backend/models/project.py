from datetime import datetime
from enum import Enum

from pydantic import BaseModel


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
    panorama_path: str | None = None
    error: str | None = None


PROJECTS: dict[str, Project] = {}
PROJECT_PROGRESS: dict[str, int] = {}
PROJECT_STAGE_RESULTS: dict[str, dict] = {}
PROJECT_WORLD_RESULTS: dict[str, dict] = {}
