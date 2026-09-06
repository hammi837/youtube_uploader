"""
backend/tts_models.py — SQLAlchemy ORM models and Pydantic schemas
for Phase 2B: Text-to-Speech audio generation.

Kept separate from content_models.py and models.py to avoid touching
existing Phase 1/2A code.  All models use the same Base from backend.db
so tables are registered under the same metadata and auto-created.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


# ── Audio generation statuses ─────────────────────────────────────────────────

class AudioStatus:
    PENDING    = "pending"
    GENERATING = "generating"
    COMPLETED  = "completed"
    FAILED     = "failed"

    ALL = {PENDING, GENERATING, COMPLETED, FAILED}


# ── SQLAlchemy model ──────────────────────────────────────────────────────────

class GeneratedAudio(Base):
    """
    Persisted TTS generation record.

    One GeneratedAudio → one MP3 file on disk.
    Linked to a ContentProject (which owns the script).
    """
    __tablename__ = "generated_audio"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    content_project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("content_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    voice: Mapped[str]           = mapped_column(String(100), nullable=False)
    language: Mapped[str]        = mapped_column(String(10),  nullable=False, default="en")
    text_length: Mapped[int]     = mapped_column(nullable=False, default=0)
    file_path: Mapped[Optional[str]]  = mapped_column(String(1000), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(nullable=True)
    status: Mapped[str]          = mapped_column(String(20), nullable=False, default=AudioStatus.PENDING)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Phase 2C: which provider actually generated the audio
    provider: Mapped[Optional[str]]      = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(), default=func.now(),
    )

    # Relationship back to ContentProject (no cascade from this side —
    # ContentProject already cascades its own children)
    project: Mapped["backend.content_models.ContentProject"] = relationship(  # type: ignore[name-defined]
        "ContentProject",
        back_populates="audio_records",
        foreign_keys=[content_project_id],
    )


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TTSGenerateRequest(BaseModel):
    """Body for POST /api/tts/generate."""
    text: str           = Field(..., min_length=1, max_length=20000)
    voice: Optional[str] = Field(None, description="Voice name; uses TTS_DEFAULT_VOICE if omitted.")
    language: str       = Field("en", min_length=2, max_length=10)

    @field_validator("text")
    @classmethod
    def text_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text cannot be blank")
        return v


class TTSGenerateFromContentRequest(BaseModel):
    """Body for POST /api/tts/generate-from-content/{content_project_id}."""
    voice: Optional[str] = Field(None, description="Voice name; uses TTS_DEFAULT_VOICE if omitted.")


class AudioResponse(BaseModel):
    """API response for a generated audio record."""
    id: str
    content_project_id: str
    voice: str
    language: str
    text_length: int
    file_size_bytes: Optional[int]
    duration_seconds: Optional[float]
    status: str
    error_message: Optional[str]
    # Phase 2C: which provider generated the audio ("edge", "local", or None)
    provider: Optional[str]
    created_at: datetime
    updated_at: datetime
    # Streaming URL — served via GET /api/tts/{audio_id}/audio
    audio_url: Optional[str]

    model_config = {"from_attributes": True}


class TTSVoiceResponse(BaseModel):
    """Single voice entry returned by GET /api/tts/voices."""
    name: str
    locale: str
    language: str
    gender: str
    provider: str = "edge"   # "edge" | "local"


# ── ORM → Pydantic factory ────────────────────────────────────────────────────

def audio_to_response(audio: GeneratedAudio) -> AudioResponse:
    """Convert a GeneratedAudio ORM record to an AudioResponse."""
    now = datetime.now(timezone.utc)
    audio_url = (
        f"/api/tts/{audio.id}/audio"
        if audio.status == AudioStatus.COMPLETED and audio.file_path
        else None
    )
    return AudioResponse(
        id=audio.id,
        content_project_id=audio.content_project_id,
        voice=audio.voice,
        language=audio.language,
        text_length=audio.text_length,
        file_size_bytes=audio.file_size_bytes,
        duration_seconds=audio.duration_seconds,
        status=audio.status,
        error_message=audio.error_message,
        provider=audio.provider,
        created_at=audio.created_at or now,
        updated_at=audio.updated_at or now,
        audio_url=audio_url,
    )
