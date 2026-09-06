"""
backend/content_models.py — SQLAlchemy ORM models and Pydantic schemas
for Phase 2A: AI Research + Script Generation.

Kept separate from models.py to avoid touching the YouTube upload models.
Both files import Base from backend.db so all tables are registered
under the same metadata.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


# ── Content project statuses ──────────────────────────────────────────────────

class ContentStatus:
    PENDING    = "pending"
    RESEARCHING = "researching"
    GENERATING  = "generating"
    COMPLETED   = "completed"
    FAILED      = "failed"

    ALL = {PENDING, RESEARCHING, GENERATING, COMPLETED, FAILED}


# ── SQLAlchemy models ─────────────────────────────────────────────────────────

class ContentProject(Base):
    """
    Top-level record for a content generation job.

    One ContentProject → one ResearchSource set → one GeneratedScriptRecord.
    """
    __tablename__ = "content_projects"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    topic: Mapped[str]          = mapped_column(String(500), nullable=False)
    language: Mapped[str]       = mapped_column(String(10),  nullable=False, default="en")
    tone: Mapped[str]           = mapped_column(String(50),  nullable=False, default="informative")
    target_duration_seconds: Mapped[int] = mapped_column(nullable=False, default=180)
    scene_count: Mapped[int]    = mapped_column(nullable=False, default=12)
    status: Mapped[str]         = mapped_column(String(20),  nullable=False, default=ContentStatus.PENDING)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(), default=func.now(),
    )

    # Relationships
    sources: Mapped[list["ResearchSourceRecord"]] = relationship(
        "ResearchSourceRecord", back_populates="project", cascade="all, delete-orphan",
    )
    script: Mapped[Optional["GeneratedScriptRecord"]] = relationship(
        "GeneratedScriptRecord", back_populates="project",
        cascade="all, delete-orphan", uselist=False,
    )
    audio_records: Mapped[list["backend.tts_models.GeneratedAudio"]] = relationship(  # type: ignore[name-defined]
        "GeneratedAudio", back_populates="project", cascade="all, delete-orphan",
    )


class ResearchSourceRecord(Base):
    """Persisted research source linked to a ContentProject."""
    __tablename__ = "research_sources"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    content_project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("content_projects.id", ondelete="CASCADE"), nullable=False,
    )
    title: Mapped[str]   = mapped_column(String(500), nullable=False)
    url: Mapped[str]     = mapped_column(String(2000), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # key_points stored as JSON array string
    source_data: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    project: Mapped["ContentProject"] = relationship(
        "ContentProject", back_populates="sources",
    )

    def key_points_as_list(self) -> list[str]:
        try:
            return json.loads(self.source_data)
        except (json.JSONDecodeError, TypeError):
            return []


class GeneratedScriptRecord(Base):
    """Persisted generated script linked to a ContentProject."""
    __tablename__ = "generated_scripts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    content_project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("content_projects.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    title: Mapped[str]       = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text,        nullable=False, default="")
    hook: Mapped[str]        = mapped_column(Text,        nullable=False, default="")
    # tags stored as JSON array string
    tags_json: Mapped[str]   = mapped_column(Text, nullable=False, default="[]")
    # scenes stored as JSON array string
    scenes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    estimated_duration_seconds: Mapped[int] = mapped_column(nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    project: Mapped["ContentProject"] = relationship(
        "ContentProject", back_populates="script",
    )

    def tags_as_list(self) -> list[str]:
        try:
            return json.loads(self.tags_json)
        except (json.JSONDecodeError, TypeError):
            return []

    def scenes_as_list(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.scenes_json)
        except (json.JSONDecodeError, TypeError):
            return []


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class Scene(BaseModel):
    """A single scene in the generated script."""
    scene_number: int
    narration: str
    visual_description: str
    estimated_duration_seconds: int = Field(ge=1)

    model_config = {"from_attributes": True}


class GeneratedScript(BaseModel):
    """The structured script output from the LLM."""
    title: str           = Field(..., max_length=100)
    description: str     = Field(default="")
    tags: list[str]      = Field(default_factory=list)
    hook: str            = Field(default="")
    estimated_duration_seconds: int = Field(ge=0)
    scenes: list[Scene]  = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title cannot be empty")
        return v[:100]

    @field_validator("tags")
    @classmethod
    def tags_max_count(cls, v: list[str]) -> list[str]:
        return v[:30]


class ScriptRequest(BaseModel):
    """Request body for POST /api/content/generate-script."""
    topic: str           = Field(..., min_length=1, max_length=300)
    language: str        = Field("en", min_length=2, max_length=10)
    tone: str            = Field("informative", max_length=50)
    target_duration_seconds: int = Field(180, ge=30, le=3600)
    scene_count: int     = Field(12, ge=3, le=30)

    @field_validator("topic")
    @classmethod
    def topic_not_whitespace(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("topic cannot be blank")
        return stripped


class ResearchRequest(BaseModel):
    """Request body for POST /api/content/research."""
    topic: str    = Field(..., min_length=1, max_length=300)
    language: str = Field("en", min_length=2, max_length=10)
    depth: str    = Field("standard")

    @field_validator("topic")
    @classmethod
    def topic_not_whitespace(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("topic cannot be blank")
        return stripped

    @field_validator("depth")
    @classmethod
    def validate_depth(cls, v: str) -> str:
        if v not in ("quick", "standard", "deep"):
            raise ValueError("depth must be 'quick', 'standard', or 'deep'")
        return v


# ── API response schemas ──────────────────────────────────────────────────────

class ResearchSourceResponse(BaseModel):
    """Single research source in API responses."""
    title: str
    url: str
    snippet: str
    key_points: list[str]

    model_config = {"from_attributes": True}


class ResearchResponse(BaseModel):
    """Response for POST /api/content/research."""
    topic: str
    sources: list[ResearchSourceResponse]
    key_facts: list[str]
    research_summary: str


class ContentProjectResponse(BaseModel):
    """Summary of a content project (list view)."""
    id: str
    topic: str
    language: str
    tone: str
    target_duration_seconds: int
    scene_count: int
    status: str
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    has_script: bool

    model_config = {"from_attributes": True}


class ContentProjectDetailResponse(BaseModel):
    """Full detail of a content project including script and sources."""
    id: str
    topic: str
    language: str
    tone: str
    target_duration_seconds: int
    scene_count: int
    status: str
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    sources: list[ResearchSourceResponse]
    script: Optional[GeneratedScript]

    model_config = {"from_attributes": True}


# ── ORM → Pydantic factories ──────────────────────────────────────────────────

def project_to_summary(project: ContentProject) -> ContentProjectResponse:
    return ContentProjectResponse(
        id=project.id,
        topic=project.topic,
        language=project.language,
        tone=project.tone,
        target_duration_seconds=project.target_duration_seconds,
        scene_count=project.scene_count,
        status=project.status,
        error_message=project.error_message,
        created_at=project.created_at or datetime.utcnow(),
        updated_at=project.updated_at or datetime.utcnow(),
        has_script=project.script is not None,
    )


def project_to_detail(project: ContentProject) -> ContentProjectDetailResponse:
    sources = [
        ResearchSourceResponse(
            title=s.title,
            url=s.url,
            snippet=s.snippet,
            key_points=s.key_points_as_list(),
        )
        for s in project.sources
    ]

    script: Optional[GeneratedScript] = None
    if project.script:
        rec = project.script
        script = GeneratedScript(
            title=rec.title,
            description=rec.description,
            hook=rec.hook,
            tags=rec.tags_as_list(),
            estimated_duration_seconds=rec.estimated_duration_seconds,
            scenes=[Scene(**sc) for sc in rec.scenes_as_list()],
        )

    return ContentProjectDetailResponse(
        id=project.id,
        topic=project.topic,
        language=project.language,
        tone=project.tone,
        target_duration_seconds=project.target_duration_seconds,
        scene_count=project.scene_count,
        status=project.status,
        error_message=project.error_message,
        created_at=project.created_at or datetime.utcnow(),
        updated_at=project.updated_at or datetime.utcnow(),
        sources=sources,
        script=script,
    )
