from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers.projects import router as projects_router
from storage.local import ensure_dirs

app = FastAPI(title="360Flow API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    ensure_dirs()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(projects_router, prefix="/api/v1")
