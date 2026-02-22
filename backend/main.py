from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.projects import router as projects_router
from storage.local import ensure_dirs


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    yield


app = FastAPI(title="360Flow API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
