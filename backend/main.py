import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth import require_api_key
from config import get_settings
from db import fail_interrupted_jobs, init_db
from routers.projects import router as projects_router
from routers.share import router as share_router
from storage import init_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    init_db(settings)
    init_storage(settings)
    interrupted = fail_interrupted_jobs()
    if interrupted:
        logging.getLogger(__name__).warning("Marked %d interrupted jobs as failed", interrupted)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="360Flow API", version="0.2.0", lifespan=lifespan)
    origins = settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(projects_router, prefix="/api/v1", dependencies=[Depends_api_key()])
    app.include_router(share_router)

    @app.get("/health")
    async def health():
        current = get_settings()
        return {
            "status": "ok",
            "storage": current.storage_backend_name,
            "database": current.database_backend_name,
            "staging": current.staging_mode,
            "world": current.world_mode,
            "auth": current.auth_mode,
        }

    return app


def Depends_api_key():
    from fastapi import Depends

    return Depends(require_api_key)


app = create_app()
