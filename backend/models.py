"""
models.py — SQLAlchemy ORM models + Pydantic request/response schemas.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from pydantic import BaseModel, Field, field_validator

from backend.db import Base


# ── Upload job statuses ────────────────────────────────────────────────────────

class JobStatus:
    PENDING    = "pending"
    UPLOADING  = "uploading"
    SCHEDULED  = "scheduled"
    PUBLISHED  = "published"
    FAILED     = "failed"
    CANCELLED  = "cancelled"

    ALL = {PENDING, UPLOADING, SCHEDULED, PUBLISHED, FAILED, CANCELLED}


# ── SQLAlchemy model ───────────────────────────────────────────────────────────

class UploadJob(Base):
    """Represents a YouTube upload job and its current state."""
    __tablename__ = "upload_jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    video_id: Mapped[Optional[str]]  = mapped_column(String(20),  nullable=True)
    filename: Mapped[str]            = mapped_column(String(500), nullable=False)
    title: Mapped[str]               = mapped_column(String(100), nullable=False)
    description: Mapped[str]         = mapped_column(Text,        nullable=False, default="")
    tags: Mapped[str]                = mapped_column(Text,        nullable=False, default="")
    # tags stored as comma-separated string; serialised/deserialised in schemas

    category_id: Mapped[str]         = mapped_column(String(10),  nullable=False, default="22")
    privacy_status: Mapped[str]      = mapped_column(String(20),  nullable=False, default="private")
    status: Mapped[str]              = mapped_column(String(20),  nullable=False, default=JobStatus.PENDING)

    progress: Mapped[int]            = mapped_column(Integer,     nullable=False, default=0)
    # progress: 0-100 (percent)

    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:   Mapped[datetime]           = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(),
    )
    updated_at:   Mapped[datetime]           = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        default=func.now(),
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def tags_as_list(self) -> list[str]:
        if not self.tags:
            return []
        return [t.strip() for t in self.tags.split(",") if t.strip()]


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class UploadRequest(BaseModel):
    """Body for POST /api/uploads (multipart; file comes via Form/File)."""
    title: str          = Field(..., min_length=1, max_length=100)
    description: str    = Field("", max_length=5000)
    tags: list[str]     = Field(default_factory=list)
    category_id: str    = Field("22")
    privacy_status: str = Field("private")
    scheduled_at: Optional[str] = Field(
        None,
        description=(
            "ISO 8601 datetime with explicit UTC offset. "
            "Example: '2026-09-05T13:00:00+05:00' or '2026-09-05T08:00:00Z'. "
            "When provided, video uploads as private and publishes at this time."
        ),
    )
    timezone: Optional[str] = Field(
        None,
        description=(
            "IANA timezone name (e.g. 'Asia/Karachi'). "
            "Required only if scheduled_at has no UTC offset."
        ),
    )

    @field_validator("privacy_status")
    @classmethod
    def validate_privacy(cls, v: str) -> str:
        if v not in ("private", "unlisted", "public"):
            raise ValueError("privacy_status must be 'private', 'unlisted', or 'public'")
        return v

    @field_validator("category_id")
    @classmethod
    def validate_category(cls, v: str) -> str:
        try:
            int(v)
        except ValueError:
            raise ValueError("category_id must be a numeric string")
        return v


class UpdateVideoRequest(BaseModel):
    """Body for PATCH /api/videos/{video_id}."""
    title: Optional[str]          = Field(None, max_length=100)
    description: Optional[str]    = Field(None, max_length=5000)
    tags: Optional[list[str]]     = None
    privacy_status: Optional[str] = None
    scheduled_at: Optional[str]   = Field(
        None,
        description="New scheduled publish time (ISO 8601 with offset). "
                    "Pass null to clear the schedule.",
    )
    clear_schedule: bool = Field(
        False,
        description="If true, removes the publishAt and keeps the video private.",
    )
    timezone: Optional[str] = None

    @field_validator("privacy_status")
    @classmethod
    def validate_privacy(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("private", "unlisted", "public"):
            raise ValueError("privacy_status must be 'private', 'unlisted', or 'public'")
        return v


class JobStatusResponse(BaseModel):
    """Response for GET /api/uploads/{job_id}/status and job list items."""
    job_id: str
    video_id: Optional[str]
    filename: str
    title: str
    description: str
    tags: list[str]
    category_id: str
    privacy_status: str
    status: str
    progress: int
    scheduled_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    error_message: Optional[str]
    url: Optional[str]

    model_config = {"from_attributes": True}

    @classmethod
    def from_job(cls, job: UploadJob) -> "JobStatusResponse":
        now = datetime.now()
        return cls(
            job_id        = job.id,
            video_id      = job.video_id,
            filename      = job.filename,
            title         = job.title,
            description   = job.description,
            tags          = job.tags_as_list(),
            category_id   = job.category_id,
            privacy_status= job.privacy_status,
            status        = job.status,
            progress      = job.progress,
            scheduled_at  = job.scheduled_at,
            created_at    = job.created_at or now,
            updated_at    = job.updated_at or now,
            error_message = job.error_message,
            url           = (
                f"https://www.youtube.com/watch?v={job.video_id}"
                if job.video_id else None
            ),
        )


class VideoUpdateResponse(BaseModel):
    """Response for PATCH /api/videos/{video_id}."""
    video_id: str
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str


class AuthStatusResponse(BaseModel):
    authenticated: bool
    message: str
