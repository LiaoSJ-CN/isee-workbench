"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import data_source as _data_source_module  # noqa: F401
from app.models import report as _report_module  # noqa: F401
from app.routers import auth, data_source, explorer, report, scheduler
from app.services.scheduler import get_scheduler

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    scheduler = get_scheduler()
    db = SessionLocal()
    try:
        scheduler.sync_with_database(db)
        scheduler.start()
    finally:
        db.close()

    yield

    # Shutdown
    scheduler.shutdown()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(data_source.router)
app.include_router(report.router)
app.include_router(scheduler.router)
app.include_router(explorer.router)

# Serve locally-bundled Chart.js so generated HTML previews work without external CDN.
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Serve locally-bundled Chart.js so generated HTML previews work without external CDN.
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}
