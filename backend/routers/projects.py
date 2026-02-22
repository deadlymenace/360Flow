import asyncio
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from models.project import (
    PROJECT_PROGRESS,
    PROJECT_STAGE_RESULTS,
    PROJECT_WORLD_RESULTS,
    PROJECTS,
    Project,
    ProjectStatus,
)
from services.staging_mock import mock_stage
from services.stitcher import stitch_images
from services.world_gen_mock import mock_generate_3d
from storage.local import UPLOADS_DIR, get_panorama_path, save_upload

router = APIRouter(tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str


class StageRequest(BaseModel):
    prompt: str


@router.post("/projects", response_model=Project)
def create_project(payload: CreateProjectRequest) -> Project:
    project_id = str(uuid.uuid4())
    project = Project(
        id=project_id,
        name=payload.name,
        status=ProjectStatus.CREATED,
        created_at=datetime.now(timezone.utc),
    )
    PROJECTS[project_id] = project
    PROJECT_PROGRESS[project_id] = 0
    return project


@router.get("/projects", response_model=list[Project])
def list_projects() -> list[Project]:
    return sorted(PROJECTS.values(), key=lambda p: p.created_at, reverse=True)


@router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: str) -> Project:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _run_stitching(project_id: str) -> None:
    project = PROJECTS.get(project_id)
    if not project:
        return

    try:
        PROJECT_PROGRESS[project_id] = 50
        project.status = ProjectStatus.STITCHING
        image_paths = list((UPLOADS_DIR / project_id).glob("*"))
        output_path = get_panorama_path(project_id)
        ok = stitch_images(image_paths=image_paths, output_path=output_path)
        if not ok:
            project.status = ProjectStatus.FAILED
            project.error = "OpenCV stitching failed"
            PROJECT_PROGRESS[project_id] = 100
            return

        project.status = ProjectStatus.STITCHED
        project.panorama_path = str(output_path)
        project.error = None
        PROJECT_PROGRESS[project_id] = 100
    except Exception as exc:
        project.status = ProjectStatus.FAILED
        project.error = f"Stitching exception: {exc}"
        PROJECT_PROGRESS[project_id] = 100


@router.post("/projects/{project_id}/upload")
async def upload_images(project_id: str, images: list[UploadFile]) -> dict[str, str]:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not images:
        raise HTTPException(status_code=400, detail="No images provided")

    project.status = ProjectStatus.UPLOADING
    PROJECT_PROGRESS[project_id] = 25

    count = 0
    for idx, image in enumerate(images):
        data = await image.read()
        filename = image.filename or f"image_{idx:02d}.jpg"
        save_upload(project_id, filename, data)
        count += 1

    project.image_count = count
    project.status = ProjectStatus.STITCHING
    PROJECT_PROGRESS[project_id] = 40

    thread = threading.Thread(target=_run_stitching, args=(project_id,), daemon=True)
    thread.start()

    return {"status": "stitching"}


@router.get("/projects/{project_id}/status")
def get_project_status(project_id: str) -> dict:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return {
        "status": project.status.value,
        "progress": PROJECT_PROGRESS.get(project_id, 0),
        "error": project.error,
    }


@router.get("/projects/{project_id}/panorama")
def get_panorama(project_id: str):
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != ProjectStatus.STITCHED or not project.panorama_path:
        raise HTTPException(status_code=404, detail="Panorama not ready")

    panorama_path = Path(project.panorama_path)
    if not panorama_path.exists():
        raise HTTPException(status_code=404, detail="Panorama file missing")

    return FileResponse(path=panorama_path, media_type="image/jpeg", filename=f"{project_id}.jpg")


async def _run_stage(project_id: str, prompt: str) -> None:
    project = PROJECTS.get(project_id)
    if not project:
        return
    project.status = ProjectStatus.STAGING
    result = await mock_stage(project.panorama_path or "", prompt)
    PROJECT_STAGE_RESULTS[project_id] = result
    project.status = ProjectStatus.STAGED


@router.post("/projects/{project_id}/stage")
async def stage_project(project_id: str, payload: StageRequest, background_tasks: BackgroundTasks) -> dict:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.panorama_path:
        raise HTTPException(status_code=400, detail="Panorama not ready")

    background_tasks.add_task(asyncio.run, _run_stage(project_id, payload.prompt))
    return {"status": "staging"}


@router.get("/projects/{project_id}/stage/status")
def stage_status(project_id: str) -> dict:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status == ProjectStatus.STAGED:
        return PROJECT_STAGE_RESULTS.get(project_id, {"status": "staged"})

    return {"status": project.status.value}


async def _run_generate_3d(project_id: str) -> None:
    project = PROJECTS.get(project_id)
    if not project:
        return
    project.status = ProjectStatus.GENERATING_3D
    result = await mock_generate_3d(project.panorama_path or "")
    PROJECT_WORLD_RESULTS[project_id] = result
    project.status = ProjectStatus.COMPLETE


@router.post("/projects/{project_id}/generate-3d")
async def generate_3d(project_id: str, background_tasks: BackgroundTasks) -> dict:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.panorama_path:
        raise HTTPException(status_code=400, detail="Panorama not ready")

    background_tasks.add_task(asyncio.run, _run_generate_3d(project_id))
    return {"status": "generating_3d"}


@router.get("/projects/{project_id}/generate-3d/status")
def generate_3d_status(project_id: str) -> dict:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status == ProjectStatus.COMPLETE:
        return PROJECT_WORLD_RESULTS.get(project_id, {"status": "complete"})

    return {"status": project.status.value}
