import asyncio
import logging
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from models.project import (
    CreateProjectRequest,
    Project,
    ProjectStatus,
    StageRequest,
    projects_db,
)
from services.stitcher import stitch_images
from services.staging_mock import mock_stage
from services.world_gen_mock import mock_generate_3d
from storage.local import get_panorama_path, get_upload_images, save_upload

logger = logging.getLogger(__name__)
router = APIRouter()


def _run_stitching(project_id: str):
    """Run stitching in a background thread."""
    project = projects_db.get(project_id)
    if not project:
        return

    project.status = ProjectStatus.STITCHING
    image_paths = get_upload_images(project_id)
    output_path = get_panorama_path(project_id)

    success = stitch_images(image_paths, output_path)

    if success:
        project.status = ProjectStatus.STITCHED
        project.panorama_path = str(output_path)
        logger.info("Project %s stitching complete", project_id)
    else:
        project.status = ProjectStatus.FAILED
        project.error = "Stitching failed. Try recapturing with more overlap between photos."
        logger.error("Project %s stitching failed", project_id)


@router.post("/projects")
async def create_project(req: CreateProjectRequest) -> Project:
    project = Project(name=req.name)
    projects_db[project.id] = project
    return project


@router.get("/projects")
async def list_projects() -> list[Project]:
    return list(projects_db.values())


@router.get("/projects/{project_id}")
async def get_project(project_id: str) -> Project:
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/projects/{project_id}/upload")
async def upload_images(project_id: str, images: list[UploadFile] = File(...)):
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project.status = ProjectStatus.UPLOADING

    for i, image in enumerate(images):
        data = await image.read()
        filename = f"{i:02d}_{image.filename}"
        save_upload(project_id, filename, data)

    project.image_count = len(images)
    logger.info("Project %s: uploaded %d images", project_id, len(images))

    # Start stitching in background thread
    thread = threading.Thread(target=_run_stitching, args=(project_id,), daemon=True)
    thread.start()

    return {"status": "stitching", "image_count": len(images)}


@router.get("/projects/{project_id}/status")
async def get_status(project_id: str):
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    progress = 0
    if project.status == ProjectStatus.UPLOADING:
        progress = 25
    elif project.status == ProjectStatus.STITCHING:
        progress = 50
    elif project.status in (ProjectStatus.STITCHED, ProjectStatus.STAGED, ProjectStatus.COMPLETE):
        progress = 100

    return {
        "status": project.status.value,
        "progress": progress,
        "error": project.error,
    }


@router.get("/projects/{project_id}/panorama")
async def get_panorama(project_id: str):
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    pano_path = get_panorama_path(project_id)
    if not pano_path.exists():
        raise HTTPException(status_code=404, detail="Panorama not ready yet")

    return FileResponse(pano_path, media_type="image/jpeg")


@router.post("/projects/{project_id}/stage")
async def stage_project(project_id: str, req: StageRequest):
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.panorama_path:
        raise HTTPException(status_code=400, detail="No panorama available to stage")

    project.status = ProjectStatus.STAGING
    project.staging_prompt = req.prompt
    project.staging_started_at = datetime.now(timezone.utc)

    # Run mock staging in background
    asyncio.create_task(_async_stage(project_id, project.panorama_path, req.prompt))
    return {"status": "staging", "prompt": req.prompt}


async def _async_stage(project_id: str, panorama_path: str, prompt: str):
    result = await mock_stage(panorama_path, prompt)
    project = projects_db.get(project_id)
    if project:
        project.status = ProjectStatus.STAGED


@router.get("/projects/{project_id}/stage/status")
async def get_stage_status(project_id: str):
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return {
        "status": project.status.value,
        "prompt": project.staging_prompt,
        "provider": "nanobanana_mock",
    }


@router.post("/projects/{project_id}/generate-3d")
async def generate_3d(project_id: str):
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.panorama_path:
        raise HTTPException(status_code=400, detail="No panorama available")

    project.status = ProjectStatus.GENERATING_3D
    project.gen3d_started_at = datetime.now(timezone.utc)

    asyncio.create_task(_async_generate_3d(project_id, project.panorama_path))
    return {"status": "generating_3d"}


async def _async_generate_3d(project_id: str, panorama_path: str):
    result = await mock_generate_3d(panorama_path)
    project = projects_db.get(project_id)
    if project:
        project.status = ProjectStatus.COMPLETE


@router.get("/projects/{project_id}/generate-3d/status")
async def get_generate_3d_status(project_id: str):
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    assets = None
    if project.status == ProjectStatus.COMPLETE:
        assets = {
            "splat_url": "https://mock.worldlabs.ai/assets/fake-splat.spz",
            "mesh_url": "https://mock.worldlabs.ai/assets/fake-mesh.glb",
            "thumbnail_url": "https://mock.worldlabs.ai/assets/fake-thumb.jpg",
        }

    return {
        "status": project.status.value,
        "assets": assets,
        "provider": "worldlabs_mock",
    }
