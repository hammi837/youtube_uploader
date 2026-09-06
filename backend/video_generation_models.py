"""
backend/video_generation_models.py — ORM + Pydantic models for Phase 2D.

Kept separate from existing model files to avoid touching Phase 1/2A/2B/2C.
All models share the same Base from backend.db.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


# ── Job statuses ──────────────────────────────────────────────────────────────

class VideoJobStatus:
    QUEUED              = "queued"
    PREPARING           = "preparing"
    GENERATING_AUDIO    = "generating_audio"
    PREPARING_VISUALS   = "preparing_visuals"
    GENERATING_CAPTIONS = "generating_captions"
    ASSEMBLING          = "assembling"
    GENERATING_THUMBNAIL = "generating_thumbnail"
    COMPLETED           = "completed"
    FAILED              = "failed"
    CANCELLED           = "cancelled"

    ALL = {
        QUEUED, PREPARING, GENERATING_AUDIO, PREPARING_VISUALS,
        GENERATING_CAPTIONS, ASSEMBLING, GENERATING_THUMBNAIL,
        COMPLETED, FAILED, CANCELLED,
    }
    TERMINAL = {COMPLETED, FAILED, CANCELLED}


# ── SQLAlchemy model ──────────────────────────────────────────────────────────

class VideoGenerationJob(Base):
    """Persisted video-generation job record."""
    __tablename__ = "video_generation_jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    content_project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("content_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    # audio_id: optional — may be None if audio was generated inline
    audio_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("generated_audio.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=VideoJobStatus.QUEUED,
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_step: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Output file paths (absolute, G: drive only)
    output_path: Mapped[Optional[str]]    = mapped_column(String(1000), nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    caption_path: Mapped[Optional[str]]   = mapped_column(String(1000), nullable=True)

    # Video metadata
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[int]  = mapped_column(Integer, nullable=False, default=1920)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=1080)
    fps: Mapped[int]    = mapped_column(Integer, nullable=False, default=30)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Settings used for this job
    captions_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    music_enabled: Mapped[bool]    = mapped_column(nullable=False, default=True)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(), default=func.now(),
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Relationships
    project: Mapped["backend.content_models.ContentProject"] = relationship(  # type: ignore
        "ContentProject", foreign_keys=[content_project_id],
    )
    audio: Mapped[Optional["backend.tts_models.GeneratedAudio"]] = relationship(  # type: ignore
        "GeneratedAudio", foreign_keys=[audio_id],
    )


# ── Pydantic request/response schemas ────────────────────────────────────────

class VideoGenerationRequest(BaseModel):
    """POST /api/video-generation/from-content/{project_id}"""
    audio_id: Optional[str]      = Field(None, description="Existing audio record ID to use")
    width: int                   = Field(1920, ge=640, le=3840)
    height: int                  = Field(1080, ge=360, le=2160)
    fps: int                     = Field(30, ge=15, le=60)
    captions_enabled: bool       = Field(True)
    music_enabled: bool          = Field(True)


class VideoJobResponse(BaseModel):
    """API response for a video-generation job."""
    id: str
    content_project_id: str
    audio_id: Optional[str]
    status: str
    progress: int
    current_step: Optional[str]
    output_path: Optional[str]    = None   # never exposed raw — use /video endpoint
    thumbnail_path: Optional[str] = None   # never exposed raw
    caption_path: Optional[str]   = None   # never exposed raw
    duration_seconds: Optional[float]
    width: int
    height: int
    fps: int
    file_size_bytes: Optional[int]
    captions_enabled: bool
    music_enabled: bool
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]

    # Derived URL fields — safe ID-based links, not filesystem paths
    video_url: Optional[str]     = None
    thumbnail_url: Optional[str] = None
    caption_url: Optional[str]   = None

    model_config = {"from_attributes": True}


# ── ORM → Pydantic factory ────────────────────────────────────────────────────

def job_to_response(job: VideoGenerationJob) -> VideoJobResponse:
    now = datetime.now(timezone.utc)
    return VideoJobResponse(
        id=job.id,
        content_project_id=job.content_project_id,
        audio_id=job.audio_id,
        status=job.status,
        progress=job.progress,
        current_step=job.current_step,
        duration_seconds=job.duration_seconds,
        width=job.width,
        height=job.height,
        fps=job.fps,
        file_size_bytes=job.file_size_bytes,
        captions_enabled=job.captions_enabled,
        music_enabled=job.music_enabled,
        error_message=job.error_message,
        created_at=job.created_at or now,
        updated_at=job.updated_at or now,
        completed_at=job.completed_at,
        video_url=(
            f"/api/video-generation/{job.id}/video"
            if job.status == VideoJobStatus.COMPLETED and job.output_path
            else None
        ),
        thumbnail_url=(
            f"/api/video-generation/{job.id}/thumbnail"
            if job.thumbnail_path
            else None
        ),
        caption_url=(
            f"/api/video-generation/{job.id}/captions"
            if job.caption_path
            else None
        ),
    )
