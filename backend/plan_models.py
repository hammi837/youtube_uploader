"""
backend/plan_models.py — SQLAlchemy ORM models and Pydantic schemas
for Phase 3L: Intelligent Content Planning.

Kept separate from existing model files to avoid touching existing phases.
All models share the same Base from backend.db so tables are registered
under the same metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


# ── Plan statuses ────────────────────────────────────────────────────────────

class PlanStatus:
    DRAFT = "draft"
    APPROVED = "approved"
    COMPLETED = "completed"
    REJECTED = "rejected"

    ALL = {DRAFT, APPROVED, COMPLETED, REJECTED}

    # Valid transitions
    TRANSITIONS = {
        DRAFT: {APPROVED, REJECTED},
        APPROVED: {COMPLETED},
        REJECTED: set(),
        COMPLETED: set(),
    }


# ── SQLAlchemy model ──────────────────────────────────────────────────────────

class ContentPlan(Base):
    """
    Top-level record for a content plan.

    One ContentPlan groups multiple ContentProject records for batch
    generation, review, and approval before queueing into the production pipeline.
    """
    __tablename__ = "content_plans"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PlanStatus.DRAFT)

    # Optional schedule configuration for the entire plan
    schedule_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    schedule_interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    schedule_timezone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(), default=func.now(),
    )

    # Relationships
    projects: Mapped[list["backend.content_models.ContentProject"]] = relationship(
        "ContentProject", back_populates="plan",
        cascade="all, delete-orphan",
    )


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ContentPlanCreate(BaseModel):
    """Request body for POST /api/plans."""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    schedule_start: Optional[str] = Field(
        None,
        description="ISO 8601 UTC datetime for the first video's publish time. "
                    "E.g. '2026-10-01T20:00:00+05:00'",
    )
    schedule_interval_minutes: Optional[int] = Field(
        None,
        ge=5,
        le=10080,
        description="Minutes between consecutive scheduled videos (default 1440 = 1 day).",
    )
    schedule_timezone: Optional[str] = Field(
        None,
        description="IANA timezone for the schedule_start datetime if no offset is given.",
    )

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be blank")
        return v.strip()[:200]

    @field_validator("schedule_timezone")
    @classmethod
    def validate_timezone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        # Basic validation - will be fully validated by scheduler service
        return v


class ContentPlanUpdate(BaseModel):
    """Request body for PUT /api/plans/{plan_id}."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)
    status: Optional[str] = Field(None)
    schedule_start: Optional[str] = Field(None)
    schedule_interval_minutes: Optional[int] = Field(None, ge=5, le=10080)
    schedule_timezone: Optional[str] = Field(None)

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not v.strip():
            raise ValueError("name cannot be blank")
        return v.strip()[:200]

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in PlanStatus.ALL:
            raise ValueError(f"status must be one of {PlanStatus.ALL}")
        return v


class ContentPlanResponse(BaseModel):
    """API response for a content plan."""
    id: str
    name: str
    description: Optional[str]
    status: str
    schedule_start: Optional[datetime]
    schedule_interval_minutes: Optional[int]
    schedule_timezone: Optional[str]
    created_at: datetime
    updated_at: datetime
    item_count: int = 0  # Number of ContentProjects in this plan

    model_config = {"from_attributes": True}


class PlanItemCreate(BaseModel):
    """Request body for POST /api/plans/{plan_id}/items."""
    topic: str = Field(..., min_length=1, max_length=300)
    language: str = Field("en", min_length=2, max_length=10)
    tone: str = Field("engaging", max_length=50)
    target_duration_seconds: int = Field(180, ge=30, le=3600)
    scene_count: int = Field(12, ge=3, le=30)

    @field_validator("topic")
    @classmethod
    def topic_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("topic cannot be blank")
        return v.strip()


class PlanItemUpdate(BaseModel):
    """Request body for PUT /api/plans/{plan_id}/items/{item_id}."""
    topic: Optional[str] = Field(None, min_length=1, max_length=300)
    language: Optional[str] = Field(None, min_length=2, max_length=10)
    tone: Optional[str] = Field(None, max_length=50)
    target_duration_seconds: Optional[int] = Field(None, ge=30, le=3600)
    scene_count: Optional[int] = Field(None, ge=3, le=30)

    @field_validator("topic")
    @classmethod
    def topic_not_whitespace(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not v.strip():
            raise ValueError("topic cannot be blank")
        return v.strip()


class PlanItemResponse(BaseModel):
    """API response for a plan item (ContentProject)."""
    id: str
    plan_id: Optional[str]
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
    script_title: Optional[str] = None

    model_config = {"from_attributes": True}


class PlanGenerateRequest(BaseModel):
    """Request body for POST /api/plans/{plan_id}/generate."""
    # No fields needed - generate all items in the plan
    pass


class PlanApproveRequest(BaseModel):
    """Request body for POST /api/plans/{plan_id}/approve."""
    # Optional: override plan-level schedule at approval time
    schedule_start: Optional[str] = Field(None)
    schedule_interval_minutes: Optional[int] = Field(None, ge=5, le=10080)
    schedule_timezone: Optional[str] = Field(None)


class PlanApproveResponse(BaseModel):
    """Response from POST /api/plans/{plan_id}/approve."""
    plan_id: str
    queued_count: int
    skipped_count: int
    schedule_summary: list[str]  # human-readable schedule info per job


# ── ORM → Pydantic factories ────────────────────────────────────────────────────

def plan_to_response(plan: ContentPlan) -> ContentPlanResponse:
    """Convert a ContentPlan ORM record to a ContentPlanResponse."""
    return ContentPlanResponse(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        status=plan.status,
        schedule_start=plan.schedule_start,
        schedule_interval_minutes=plan.schedule_interval_minutes,
        schedule_timezone=plan.schedule_timezone,
        created_at=plan.created_at or datetime.utcnow(),
        updated_at=plan.updated_at or datetime.utcnow(),
        item_count=len(plan.projects) if plan.projects else 0,
    )
