import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import get_settings
from db import fail_interrupted_jobs, init_db
from httpguard import GuardMiddleware
from routers.devices import router as devices_router
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
    docs_url = "/docs" if settings.enable_docs else None
    openapi_url = "/openapi.json" if settings.enable_docs else None
    app = FastAPI(
        title="360Flow API",
        version="0.3.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
    )
    app.add_middleware(GuardMiddleware)
    app.include_router(devices_router, prefix="/api/v1")
    app.include_router(projects_router, prefix="/api/v1")
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
            "admin": bool(current.api_key),
        }

    return app


app = create_app()
