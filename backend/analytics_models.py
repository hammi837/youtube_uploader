"""
backend/analytics_models.py — Phase 3K analytics response models.

Defines Pydantic models for analytics API responses.
All models include data availability notes to clarify retention limitations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AnalyticsSummary(BaseModel):
    """Summary metrics for the specified date range."""
    date_range: str = Field(..., description="Date range: '7d', '30d', or 'all'")
    total_jobs: int = Field(..., description="Total jobs in date range")
    completed_jobs: int = Field(..., description="Completed jobs in date range")
    failed_jobs: int = Field(..., description="Failed jobs in date range")
    success_rate: float = Field(..., description="Success rate (completed / completed + failed)")
    avg_total_duration_seconds: Optional[float] = Field(
        None, description="Average total duration (completed_at - started_at) where both exist"
    )
    uploads_today: int = Field(..., description="Uploads today from YouTubeDailyUpload")
    current_disk_usage_gb: float = Field(..., description="Current disk usage snapshot")
    data_availability_note: str = Field(
        ...,
        description="Note about data availability based on MEDIA_RETENTION_DAYS",
    )


class PerformanceMetrics(BaseModel):
    """Performance metrics for the specified date range."""
    date_range: str = Field(..., description="Date range: '7d', '30d', or 'all'")
    jobs_completed: int = Field(..., description="Completed jobs in date range")
    jobs_with_retries: int = Field(..., description="Jobs with retry_count > 0")
    retry_rate: float = Field(..., description="Retry rate (jobs_with_retries / total_jobs)")
    avg_total_duration_seconds: Optional[float] = Field(
        None, description="Average total duration (completed_at - started_at) where both exist"
    )
    p50_duration_seconds: Optional[float] = Field(
        None, description="50th percentile of total duration"
    )
    p90_duration_seconds: Optional[float] = Field(
        None, description="90th percentile of total duration"
    )
    p95_duration_seconds: Optional[float] = Field(
        None, description="95th percentile of total duration"
    )
    data_availability_note: str = Field(
        ...,
        description="Note about data availability based on MEDIA_RETENTION_DAYS",
    )


class ErrorAggregation(BaseModel):
    """Error aggregation for the specified date range."""
    date_range: str = Field(..., description="Date range: '7d', '30d', or 'all'")
    total_errors: int = Field(..., description="Total failed jobs in date range")
    transient_errors: int = Field(..., description="Errors classified as transient")
    permanent_errors: int = Field(..., description="Errors classified as permanent")
    unknown_errors: int = Field(..., description="Errors that could not be classified")
    error_by_type: list[dict] = Field(
        default_factory=list, description="Error breakdown by last_error_type"
    )
    error_by_stage: list[dict] = Field(
        default_factory=list, description="Error breakdown by production_stage"
    )
    top_error_messages: list[str] = Field(
        default_factory=list, description="Top error messages (sanitized)"
    )
    data_availability_note: str = Field(
        ...,
        description="Note about data availability based on MEDIA_RETENTION_DAYS",
    )


class ResourceUsage(BaseModel):
    """Current resource usage (snapshot, not historical trend)."""
    disk_usage_gb: float = Field(..., description="Current total disk usage")
    free_disk_gb: float = Field(..., description="Current free disk space")
    temp_size_gb: float = Field(..., description="Current temp directory size")
    audio_size_gb: float = Field(..., description="Current audio storage size")
    video_size_gb: float = Field(..., description="Current video storage size")
    ai_images_size_gb: float = Field(..., description="Current AI images storage size")
    note: str = Field(
        ...,
        description="Note: Current snapshot - not historical trend",
    )


class AnalyticsJob(BaseModel):
    """Single job record for analytics queries."""
    id: str
    topic: str
    language: str
    tone: str
    status: str
    production_stage: Optional[str] = None
    progress: int
    retry_count: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    last_error_type: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    data_availability_note: str = Field(
        default="Data availability depends on MEDIA_RETENTION_DAYS",
    )


class AnalyticsJobsResponse(BaseModel):
    """Paginated response for job analytics queries."""
    date_range: str
    total_count: int
    page: int
    page_size: int
    jobs: list[AnalyticsJob]
    data_availability_note: str
