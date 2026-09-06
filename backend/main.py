"""
backend/main.py — FastAPI application entry point.

Start with:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Environment is loaded from (in order, later values win):
    .env           — root-level config (credentials paths, etc.)
    backend/.env   — backend-specific overrides (DATABASE_URL, UPLOAD_TEMP_DIR, etc.)
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load environment files before anything else.
# Root .env first, then backend/.env so backend-specific values can override.
_root = Path(__file__).parent.parent   # G:\youtube-uploader
load_dotenv(_root / ".env",            override=False)
load_dotenv(_root / "backend" / ".env", override=True)

from backend.db import Base, engine
from backend.routers import auth, uploads, videos
from backend.routers import content  # Phase 2A: AI content generation
from backend.routers import tts      # Phase 2B: TTS audio generation
from backend.routers import video_generation  # Phase 2D: Video generation
from backend.routers import queue    # Phase 3A: Content queue
# Import models so SQLAlchemy registers all tables under Base.metadata
import backend.content_models  # noqa: F401
import backend.tts_models       # noqa: F401
import backend.video_generation_models  # noqa: F401
import backend.queue_models     # noqa: F401


# ── Lifespan: create DB tables on startup ─────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Phase 3A: start the queue worker on backend startup
    from backend.services.queue_processor import start_worker
    start_worker()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "YouTube Uploader API",
    description = "Backend API for uploading and scheduling YouTube videos.",
    version     = "1.0.0",
    lifespan    = lifespan,
)

# CORS — allow the React dev server (port 3000 / 5173) during development.
# Tighten this for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins   = ["http://localhost:3000", "http://localhost:5173"],
    allow_methods   = ["*"],
    allow_headers   = ["*"],
    allow_credentials = True,
)


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(videos.router)
app.include_router(content.router)          # Phase 2A
app.include_router(tts.router)             # Phase 2B
app.include_router(video_generation.router)  # Phase 2D
app.include_router(queue.router)           # Phase 3A


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/api/health", tags=["health"])
def health():
    """Simple health check — returns 200 if the server is running."""
    return {"status": "ok", "version": app.version}
