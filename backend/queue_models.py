"""
backend/queue_models.py — ORM + Pydantic models for Phase 3A: Content Queue.

Kept separate from all existing model files.
All models share the same Base from backend.db.

Queue job lifecycle:
    queued → researching → generating_script → generating_audio
           → generating_video → uploading → scheduled / completed
           → failed / cancelled

One queue job orchestrates the full pipeline:
    Research → Script → TTS → Video → Thumbnail → YouTube Upload → Schedule
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


# ── Queue job statuses ────────────────────────────────────────────────────────

class QueueStatus:
    QUEUED             = "queued"
    RESEARCHING        = "researching"
    GENERATING_SCRIPT  = "generating_script"
    GENERATING_AUDIO   = "generating_audio"
    GENERATING_VIDEO   = "generating_video"
    UPLOADING          = "uploading"
    SCHEDULED          = "scheduled"
    COMPLETED          = "completed"
    FAILED             = "failed"
    CANCELLED          = "cancelled"
    PAUSED             = "paused"

    # All valid values
    ALL = {
        QUEUED, RESEARCHING, GENERATING_SCRIPT, GENERATING_AUDIO,
        GENERATING_VIDEO, UPLOADING, SCHEDULED, COMPLETED, FAILED,
        CANCELLED, PAUSED,
    }

    # Terminal states — job will not continue processing
    TERMINAL = {COMPLETED, FAILED, CANCELLED}

    # States that mean "actively being processed" (not yet terminal, not idle)
    ACTIVE = {
        RESEARCHING, GENERATING_SCRIPT, GENERATING_AUDIO,
        GENERATING_VIDEO, UPLOADING,
    }


# ── SQLAlchemy model ──────────────────────────────────────────────────────────

class ContentQueueJob(Base):
    """
    Persistent queue job record.

    One ContentQueueJob drives the full pipeline for a single topic:
    Research → Script → TTS → Video → Thumbnail → YouTube → Schedule.

    Relationships to Phase 2 records:
      - content_project_id: set after research + script succeed (Phase 2A)
      - video_job_id:        set after video generation succeeds (Phase 2D)

    These FKs are nullable so the queue record is valid even before
    the downstream records are created.
    """
    __tablename__ = "content_queue_jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # ── User-supplied topic config ─────────────────────────────────────────
    topic: Mapped[str]                   = mapped_column(String(500), nullable=False)
    language: Mapped[str]                = mapped_column(String(10),  nullable=False, default="en")
    tone: Mapped[str]                    = mapped_column(String(50),  nullable=False, default="engaging")
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=180)
    scene_count: Mapped[int]             = mapped_column(Integer, nullable=False, default=10)

    # ── Queue config ───────────────────────────────────────────────────────
    priority: Mapped[int]  = mapped_column(Integer, nullable=False, default=0)
    # Higher priority = processed first. Default 0.

    # ── Status ────────────────────────────────────────────────────────────
    status: Mapped[str]                  = mapped_column(String(30), nullable=False, default=QueueStatus.QUEUED)
    current_stage: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    progress: Mapped[int]                = mapped_column(Integer, nullable=False, default=0)

    # ── Retry ─────────────────────────────────────────────────────────────
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # ── Scheduling config (optional) ──────────────────────────────────────
    # UTC datetime for YouTube scheduled publish.
    # None = upload immediately with configured privacy_status.
    scheduled_publish_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    youtube_privacy_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="private",
    )
    youtube_category_id: Mapped[str] = mapped_column(
        String(10), nullable=False, default="22",
    )

    # ── Phase 2A link ──────────────────────────────────────────────────────
    content_project_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("content_projects.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Phase 2D link ──────────────────────────────────────────────────────
    video_job_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("video_generation_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── YouTube output ─────────────────────────────────────────────────────
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    youtube_url: Mapped[Optional[str]]       = mapped_column(String(200), nullable=True)

    # ── Upload sub-status flags ────────────────────────────────────────────
    # Distinct tracking for each upload step (video upload vs thumbnail vs schedule)
    video_uploaded: Mapped[bool]    = mapped_column(Boolean, nullable=False, default=False)
    thumbnail_uploaded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schedule_set: Mapped[bool]      = mapped_column(Boolean, nullable=False, default=False)

    # ── Error tracking ─────────────────────────────────────────────────────
    error_message: Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    last_error_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(), default=func.now(),
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # ── Disk tracking ──────────────────────────────────────────────────────
    disk_usage_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── ORM relationships ──────────────────────────────────────────────────
    content_project: Mapped[Optional["backend.content_models.ContentProject"]] = (  # type: ignore
        relationship("ContentProject", foreign_keys=[content_project_id])
    )
    video_job: Mapped[Optional["backend.video_generation_models.VideoGenerationJob"]] = (  # type: ignore
        relationship("VideoGenerationJob", foreign_keys=[video_job_id])
    )


# ── Pydantic request / response schemas ──────────────────────────────────────

class BulkQueueRequest(BaseModel):
    """POST /api/queue — create one or more queue jobs from a list of topics."""
    topics: list[str] = Field(
        ...,
        min_length=1,
        description="List of topics, one per item. Whitespace-only topics are ignored.",
    )
    language: str            = Field("en", min_length=2, max_length=10)
    tone: str                = Field("engaging", max_length=50)
    target_duration_seconds: int = Field(180, ge=30, le=3600)
    scene_count: int         = Field(10, ge=3, le=30)
    priority: int            = Field(0)
    max_retries: int         = Field(3, ge=0, le=10)

    # ── YouTube scheduling ─────────────────────────────────────────────────
    # If schedule_start is provided, videos are scheduled sequentially
    # starting at that UTC datetime, spaced by schedule_interval_minutes.
    schedule_start: Optional[str]  = Field(
        None,
        description="ISO 8601 UTC datetime for the first video's publish time. "
                    "E.g. '2026-09-07T09:00:00+05:00'",
    )
    schedule_interval_minutes: int = Field(
        240, ge=5, le=10080,
        description="Minutes between consecutive scheduled videos (default 4h).",
    )
    schedule_timezone: Optional[str] = Field(
        None,
        description="IANA timezone for the schedule_start datetime if no offset is given.",
    )
    youtube_privacy_status: str  = Field("private")
    youtube_category_id: str     = Field("22")

    @field_validator("topics")
    @classmethod
    def clean_topics(cls, v: list[str]) -> list[str]:
        cleaned = [t.strip() for t in v if t.strip()]
        if not cleaned:
            raise ValueError("At least one non-empty topic is required.")
        if len(cleaned) > 50:
            raise ValueError("Maximum 50 topics per request.")
        # Detect duplicates within the submitted list (normalised lowercase)
        seen: set[str] = set()
        for t in cleaned:
            norm = t.lower()
            if norm in seen:
                raise ValueError(
                    f"Duplicate topic in request: '{t}'. "
                    "Remove duplicates before submitting."
                )
            seen.add(norm)
        return cleaned

    @field_validator("youtube_privacy_status")
    @classmethod
    def validate_privacy(cls, v: str) -> str:
        if v not in ("private", "unlisted", "public"):
            raise ValueError("youtube_privacy_status must be private, unlisted, or public")
        return v


class QueueJobResponse(BaseModel):
    """API response for a single queue job."""
    id: str
    topic: str
    language: str
    tone: str
    target_duration_seconds: int
    scene_count: int
    status: str
    current_stage: Optional[str]
    progress: int
    priority: int
    retry_count: int
    max_retries: int
    scheduled_publish_at: Optional[datetime]
    youtube_privacy_status: str
    youtube_category_id: str
    content_project_id: Optional[str]
    video_job_id: Optional[str]
    youtube_video_id: Optional[str]
    youtube_url: Optional[str]
    video_uploaded: bool
    thumbnail_uploaded: bool
    schedule_set: bool
    error_message: Optional[str]
    last_error_type: Optional[str]
    disk_usage_bytes: Optional[int]
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    failed_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    next_retry_at: Optional[datetime]

    model_config = {"from_attributes": True}


class QueueStatusResponse(BaseModel):
    """GET /api/queue/status — summary of queue state."""
    queue_running: bool
    queue_paused: bool
    current_job_id: Optional[str]
    total: int
    queued: int
    processing: int
    completed: int
    failed: int
    cancelled: int
    paused: int
    # Phase 3B additions
    uploads_today: int = 0
    upload_limit: int = 5
    uploads_remaining: int = 5
    free_disk_gb: float = 0.0
    disk_warning: bool = False


class BulkQueueResponse(BaseModel):
    """Response from POST /api/queue (bulk creation)."""
    created: int
    jobs: list[QueueJobResponse]
    schedule_summary: list[str]  # human-readable schedule info per job


# ── ORM → Pydantic factory ────────────────────────────────────────────────────

def queue_job_to_response(job: ContentQueueJob) -> QueueJobResponse:
    now = datetime.now(timezone.utc)
    return QueueJobResponse(
        id=job.id,
        topic=job.topic,
        language=job.language,
        tone=job.tone,
        target_duration_seconds=job.target_duration_seconds,
        scene_count=job.scene_count,
        status=job.status,
        current_stage=job.current_stage,
        progress=job.progress,
        priority=job.priority,
        retry_count=job.retry_count,
        max_retries=job.max_retries,
        scheduled_publish_at=job.scheduled_publish_at,
        youtube_privacy_status=job.youtube_privacy_status,
        youtube_category_id=job.youtube_category_id,
        content_project_id=job.content_project_id,
        video_job_id=job.video_job_id,
        youtube_video_id=job.youtube_video_id,
        youtube_url=job.youtube_url,
        video_uploaded=job.video_uploaded,
        thumbnail_uploaded=job.thumbnail_uploaded,
        schedule_set=job.schedule_set,
        error_message=job.error_message,
        last_error_type=getattr(job, "last_error_type", None),
        disk_usage_bytes=getattr(job, "disk_usage_bytes", None),
        created_at=job.created_at or now,
        updated_at=job.updated_at or now,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failed_at=getattr(job, "failed_at", None),
        cancelled_at=getattr(job, "cancelled_at", None),
        next_retry_at=getattr(job, "next_retry_at", None),
    )


# ── Phase 3B: Job log model ───────────────────────────────────────────────────

class QueueJobLog(Base):
    """Per-job log entries for Phase 3B diagnostics."""
    __tablename__ = "queue_job_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("content_queue_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    level: Mapped[str]           = mapped_column(String(10), nullable=False, default="info")
    stage: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    message: Mapped[str]         = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


# ── Phase 3B: Daily YouTube upload tracker ────────────────────────────────────

class YouTubeDailyUpload(Base):
    """One row per calendar date (UTC) tracking upload count."""
    __tablename__ = "youtube_daily_uploads"

    date: Mapped[str]      = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    count: Mapped[int]     = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(), default=func.now(),
    )


# ── Phase 3B: Pydantic schemas ────────────────────────────────────────────────

class QueueJobLogResponse(BaseModel):
    id: str
    job_id: str
    level: str
    stage: Optional[str]
    message: str
    created_at: datetime
    model_config = {"from_attributes": True}


class CleanupResponse(BaseModel):
    files_deleted: int
    bytes_freed: int
    files_skipped: int
    errors: list[str]


class QueueHealthResponse(BaseModel):
    status: str          # "ok" | "warning" | "error"
    worker_alive: bool
    worker_paused: bool
    free_disk_gb: float
    disk_warning: bool
    uploads_today: int
    upload_limit: int
    uploads_remaining: int
    queued_jobs: int
    active_jobs: int
    details: list[str]


class QueueStatsResponse(BaseModel):
    total: int
    queued: int
    processing: int
    completed: int
    failed: int
    cancelled: int
    scheduled: int
    uploads_today: int
    upload_limit: int
    free_disk_gb: float
    avg_processing_minutes: float
