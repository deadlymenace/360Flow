import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from auth import _admin_key_matches
from config import get_settings
from db import fail_interrupted_jobs, init_db
from services.pipeline import resume_durable_jobs, retry_delayed_worlds
from httpguard import GuardMiddleware
from routers.devices import router as devices_router
from routers.projects import router as projects_router
from routers.share import router as share_router
from services.world_gen import retry_pending_world_deletes, world_credits_balance
from storage import init_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    init_db(settings)
    init_storage(settings)
    resumed = await resume_durable_jobs()
    if resumed:
        logging.getLogger(__name__).warning("Resumed %d durable jobs", resumed)
    released = await retry_delayed_worlds()
    if released:
        logging.getLogger(__name__).info("Released %d delayed 3D jobs", released)
    interrupted = fail_interrupted_jobs()
    if interrupted:
        logging.getLogger(__name__).warning("Marked %d interrupted jobs as failed", interrupted)
    stop = asyncio.Event()

    async def _retry_deletes() -> None:
        while not stop.is_set():
            try:
                await retry_pending_world_deletes()
            except Exception:
                logging.getLogger(__name__).exception("World delete retry failed")
            try:
                await retry_delayed_worlds()
            except Exception:
                logging.getLogger(__name__).exception("Delayed world retry failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                continue

    task = None
    if not settings.jobs_inline:
        task = asyncio.create_task(_retry_deletes())
    yield
    stop.set()
    if task is not None:
        task.cancel()


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

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        logging.getLogger("validation").info(
            "Rejected invalid %s %s (%d errors)",
            request.method,
            request.url.path,
            len(exc.errors()),
        )
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    app.include_router(devices_router, prefix="/api/v1")
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(share_router)

    @app.get("/health")
    async def health(x_api_key: str | None = Header(default=None)):
        current = get_settings()
        body = {
            "status": "ok",
            "storage": current.storage_backend_name,
            "database": current.database_backend_name,
            "staging": current.staging_mode,
            "world": current.world_mode,
            "auth": current.auth_mode,
            "admin": bool(current.api_key),
        }
        if _admin_key_matches(x_api_key):
            remaining = await world_credits_balance()
            body["worldlabs_credits"] = remaining
            threshold = current.worldlabs_low_credit_threshold
            body["worldlabs_low_balance"] = remaining is not None and remaining < threshold
        return body

    return app


app = create_app()
