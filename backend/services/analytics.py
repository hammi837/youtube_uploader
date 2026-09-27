"""
backend/services/analytics.py — Phase 3K analytics service.

Provides analytics aggregation and querying functionality.
All queries are read-only and based on truthful, available data.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from backend.analytics_models import (
    AnalyticsJob,
    AnalyticsJobsResponse,
    AnalyticsSummary,
    ErrorAggregation,
    PerformanceMetrics,
    ResourceUsage,
)
from backend.db import SessionLocal
from backend.queue_models import ContentQueueJob, QueueStatus, YouTubeDailyUpload
from backend.services.queue_services import (
    check_disk_space,
    get_media_retention_days,
    get_upload_limit,
)

logger = logging.getLogger(__name__)

# ── Error classification keywords (from queue_processor.py) ─────────────────

_TRANSIENT_KEYWORDS = {
    "connection",
    "network",
    "refused",
    "ssl",
    "ttl",
    "temporary",
    "duckduckgo rate",
    "too many requests",
    "max retries exceeded",
    "retriable",
    "location: header",
    "redirected",
}

# Known permanent error types (can be extended)
_PERMANENT_ERROR_TYPES = {
    "FileNotFoundError",
    "ValueError",
    "ValidationError",
    "AuthenticationError",
    "PermissionError",
}

# Known transient error types (can be extended)
_TRANSIENT_ERROR_TYPES = {
    "TimeoutError",
    "ConnectionError",
    "HTTPError",
}


# ── Public API ────────────────────────────────────────────────────────────────


def compute_summary_metrics(days: str = "7") -> AnalyticsSummary:
    """
    Compute summary metrics for the specified date range.

    Args:
        days: Date range - "7", "30", or "all"

    Returns:
        AnalyticsSummary with calculated metrics
    """
    db = SessionLocal()
    try:
        retention_days = get_media_retention_days()
        date_filter = _build_date_filter(days, db)

        # Total jobs
        total_jobs = (
            db.query(func.count(ContentQueueJob.id))
            .filter(date_filter)
            .scalar()
            or 0
        )

        # Completed jobs
        completed_jobs = (
            db.query(func.count(ContentQueueJob.id))
            .filter(and_(date_filter, ContentQueueJob.status == QueueStatus.COMPLETED))
            .scalar()
            or 0
        )

        # Failed jobs
        failed_jobs = (
            db.query(func.count(ContentQueueJob.id))
            .filter(and_(date_filter, ContentQueueJob.status == QueueStatus.FAILED))
            .scalar()
            or 0
        )

        # Success rate
        total_completed_or_failed = completed_jobs + failed_jobs
        success_rate = (
            (completed_jobs / total_completed_or_failed) if total_completed_or_failed > 0 else 0.0
        )

        # Average total duration (only where both timestamps exist)
        duration_result = (
            db.query(
                func.avg(
                    func.julianday(ContentQueueJob.completed_at) - func.julianday(ContentQueueJob.started_at)
                )
                * 86400.0
            )
            .filter(
                and_(
                    date_filter,
                    ContentQueueJob.status == QueueStatus.COMPLETED,
                    ContentQueueJob.started_at.isnot(None),
                    ContentQueueJob.completed_at.isnot(None),
                )
            )
            .scalar()
        )
        avg_total_duration_seconds = float(duration_result) if duration_result else None

        # Uploads today
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        upload_today = (
            db.query(YouTubeDailyUpload)
            .filter(YouTubeDailyUpload.date == today_str)
            .first()
        )
        uploads_today = upload_today.count if upload_today else 0

        # Current disk usage
        disk_ok, disk_free_gb = check_disk_space()
        current_disk_usage_gb = disk_free_gb  # Use free_gb as approximation

        # Data availability note
        data_availability_note = (
            f"Based on retained records (MEDIA_RETENTION_DAYS={retention_days}). "
            f"Media files older than {retention_days} days may have been cleaned up."
        )

        return AnalyticsSummary(
            date_range=days,
            total_jobs=total_jobs,
            completed_jobs=completed_jobs,
            failed_jobs=failed_jobs,
            success_rate=success_rate,
            avg_total_duration_seconds=avg_total_duration_seconds,
            uploads_today=uploads_today,
            current_disk_usage_gb=current_disk_usage_gb,
            data_availability_note=data_availability_note,
        )
    except Exception as e:
        logger.error("Failed to compute summary metrics: %s", e)
        raise
    finally:
        db.close()


def compute_performance_metrics(days: str = "7") -> PerformanceMetrics:
    """
    Compute performance metrics for the specified date range.

    Args:
        days: Date range - "7", "30", or "all"

    Returns:
        PerformanceMetrics with calculated metrics
    """
    db = SessionLocal()
    try:
        retention_days = get_media_retention_days()
        date_filter = _build_date_filter(days, db)

        # Jobs completed
        jobs_completed = (
            db.query(func.count(ContentQueueJob.id))
            .filter(and_(date_filter, ContentQueueJob.status == QueueStatus.COMPLETED))
            .scalar()
            or 0
        )

        # Jobs with retries
        jobs_with_retries = (
            db.query(func.count(ContentQueueJob.id))
            .filter(and_(date_filter, ContentQueueJob.retry_count > 0))
            .scalar()
            or 0
        )

        # Retry rate
        total_jobs = (
            db.query(func.count(ContentQueueJob.id))
            .filter(date_filter)
            .scalar()
            or 0
        )
        retry_rate = (jobs_with_retries / total_jobs) if total_jobs > 0 else 0.0

        # Average total duration
        duration_result = (
            db.query(
                func.avg(
                    func.julianday(ContentQueueJob.completed_at) - func.julianday(ContentQueueJob.started_at)
                )
                * 86400.0
            )
            .filter(
                and_(
                    date_filter,
                    ContentQueueJob.status == QueueStatus.COMPLETED,
                    ContentQueueJob.started_at.isnot(None),
                    ContentQueueJob.completed_at.isnot(None),
                )
            )
            .scalar()
        )
        avg_total_duration_seconds = float(duration_result) if duration_result else None

        # Percentiles (P50, P90, P95)
        durations = (
            db.query(
                (func.julianday(ContentQueueJob.completed_at) - func.julianday(ContentQueueJob.started_at))
                * 86400.0
            )
            .filter(
                and_(
                    date_filter,
                    ContentQueueJob.status == QueueStatus.COMPLETED,
                    ContentQueueJob.started_at.isnot(None),
                    ContentQueueJob.completed_at.isnot(None),
                )
            )
            .all()
        )

        p50_duration_seconds = None
        p90_duration_seconds = None
        p95_duration_seconds = None

        if durations:
            sorted_durations = sorted([float(d[0]) for d in durations])
            n = len(sorted_durations)
            if n > 0:
                p50_duration_seconds = sorted_durations[int(n * 0.5)]
                p90_duration_seconds = sorted_durations[int(n * 0.9)]
                p95_duration_seconds = sorted_durations[int(n * 0.95)]

        # Data availability note
        data_availability_note = (
            f"Based on retained records (MEDIA_RETENTION_DAYS={retention_days}). "
            f"Per-stage duration metrics are not available in current data model."
        )

        return PerformanceMetrics(
            date_range=days,
            jobs_completed=jobs_completed,
            jobs_with_retries=jobs_with_retries,
            retry_rate=retry_rate,
            avg_total_duration_seconds=avg_total_duration_seconds,
            p50_duration_seconds=p50_duration_seconds,
            p90_duration_seconds=p90_duration_seconds,
            p95_duration_seconds=p95_duration_seconds,
            data_availability_note=data_availability_note,
        )
    except Exception as e:
        logger.error("Failed to compute performance metrics: %s", e)
        raise
    finally:
        db.close()


def aggregate_errors(days: str = "7") -> ErrorAggregation:
    """
    Aggregate error patterns for the specified date range.

    Args:
        days: Date range - "7", "30", or "all"

    Returns:
        ErrorAggregation with error classification and breakdown
    """
    db = SessionLocal()
    try:
        retention_days = get_media_retention_days()
        date_filter = _build_date_filter(days, db)

        # Total errors (failed jobs)
        total_errors = (
            db.query(func.count(ContentQueueJob.id))
            .filter(and_(date_filter, ContentQueueJob.status == QueueStatus.FAILED))
            .scalar()
            or 0
        )

        # Classify errors
        failed_jobs = (
            db.query(ContentQueueJob)
            .filter(and_(date_filter, ContentQueueJob.status == QueueStatus.FAILED))
            .all()
        )

        transient_errors = 0
        permanent_errors = 0
        unknown_errors = 0

        for job in failed_jobs:
            classification = _classify_error(job.last_error_type, job.error_message)
            if classification == "transient":
                transient_errors += 1
            elif classification == "permanent":
                permanent_errors += 1
            else:
                unknown_errors += 1

        # Error by type
        error_by_type = []
        error_type_counts = {}
        for job in failed_jobs:
            error_type = job.last_error_type or "unknown"
            error_type_counts[error_type] = error_type_counts.get(error_type, 0) + 1

        for error_type, count in sorted(error_type_counts.items(), key=lambda x: -x[1]):
            error_by_type.append({"type": error_type, "count": count})

        # Error by stage
        error_by_stage = []
        stage_counts = {}
        for job in failed_jobs:
            stage = job.production_stage or "unknown"
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

        for stage, count in sorted(stage_counts.items(), key=lambda x: -x[1]):
            error_by_stage.append({"stage": stage, "count": count})

        # Top error messages (sanitized)
        error_messages = [job.error_message for job in failed_jobs if job.error_message]
        # Sanitize: limit length, remove potential paths
        sanitized_messages = []
        for msg in error_messages[:10]:  # Top 10
            if msg:
                # Remove potential file paths
                sanitized = msg.replace("\\", "/").split("/")[-1][:100]
                sanitized_messages.append(sanitized)

        # Data availability note
        data_availability_note = (
            f"Based on retained records (MEDIA_RETENTION_DAYS={retention_days}). "
            f"Error classification uses existing _is_transient() logic."
        )

        return ErrorAggregation(
            date_range=days,
            total_errors=total_errors,
            transient_errors=transient_errors,
            permanent_errors=permanent_errors,
            unknown_errors=unknown_errors,
            error_by_type=error_by_type,
            error_by_stage=error_by_stage,
            top_error_messages=sanitized_messages,
            data_availability_note=data_availability_note,
        )
    except Exception as e:
        logger.error("Failed to aggregate errors: %s", e)
        raise
    finally:
        db.close()


def compute_current_resource_usage() -> ResourceUsage:
    """
    Compute current resource usage snapshots.

    Returns:
        ResourceUsage with current filesystem snapshots (NOT historical trends)
    """
    try:
        disk_ok, disk_free_gb = check_disk_space()
        data_dir = Path(os.getenv("DATA_DIR", r"G:\youtube-uploader\data"))

        # Calculate directory sizes
        temp_size_gb = _directory_size_gb(data_dir / "temp")
        audio_size_gb = _directory_size_gb(data_dir / "audio")
        video_size_gb = _directory_size_gb(data_dir / "videos")
        ai_images_size_gb = _directory_size_gb(data_dir / "generated_images")

        # Calculate total usage as free + used (approximation)
        total_gb = disk_free_gb + temp_size_gb + audio_size_gb + video_size_gb + ai_images_size_gb

        note = "Current snapshot - not historical trend. Historical resource metrics require periodic snapshot storage."

        return ResourceUsage(
            disk_usage_gb=total_gb,
            free_disk_gb=disk_free_gb,
            temp_size_gb=temp_size_gb,
            audio_size_gb=audio_size_gb,
            video_size_gb=video_size_gb,
            ai_images_size_gb=ai_images_size_gb,
            note=note,
        )
    except Exception as e:
        logger.error("Failed to compute resource usage: %s", e)
        raise


def query_jobs(
    days: str = "7",
    status: Optional[str] = None,
    language: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> AnalyticsJobsResponse:
    """
    Query jobs with optional filters and pagination.

    Args:
        days: Date range - "7", "30", or "all"
        status: Optional filter by queue status
        language: Optional filter by language
        page: Page number (1-based)
        page_size: Items per page (max 200)

    Returns:
        AnalyticsJobsResponse with paginated job data
    """
    db = SessionLocal()
    try:
        retention_days = get_media_retention_days()
        date_filter = _build_date_filter(days, db)

        # Build query
        query = db.query(ContentQueueJob).filter(date_filter)

        if status:
            query = query.filter(ContentQueueJob.status == status)

        if language:
            query = query.filter(ContentQueueJob.language == language)

        # Total count
        total_count = query.count()

        # Pagination
        page_size = min(page_size, 200)  # Max 200
        offset = (page - 1) * page_size
        jobs = query.order_by(ContentQueueJob.created_at.desc()).offset(offset).limit(page_size).all()

        # Convert to response models
        job_responses = []
        for job in jobs:
            job_responses.append(
                AnalyticsJob(
                    id=job.id,
                    topic=job.topic,
                    language=job.language,
                    tone=job.tone,
                    status=job.status,
                    production_stage=job.production_stage,
                    progress=job.progress,
                    retry_count=job.retry_count,
                    started_at=job.started_at,
                    completed_at=job.completed_at,
                    failed_at=job.failed_at,
                    last_error_type=job.last_error_type,
                    error_message=job.error_message,
                    created_at=job.created_at,
                    data_availability_note=f"Based on retained records (MEDIA_RETENTION_DAYS={retention_days})",
                )
            )

        data_availability_note = (
            f"Based on retained records (MEDIA_RETENTION_DAYS={retention_days}). "
            f"Per-stage duration metrics are not available in current data model."
        )

        return AnalyticsJobsResponse(
            date_range=days,
            total_count=total_count,
            page=page,
            page_size=page_size,
            jobs=job_responses,
            data_availability_note=data_availability_note,
        )
    except Exception as e:
        logger.error("Failed to query jobs: %s", e)
        raise
    finally:
        db.close()


# ── Internal helpers ─────────────────────────────────────────────────────────


def _build_date_filter(days: str, db: Session):
    """
    Build SQLAlchemy date filter based on days parameter.

    Args:
        days: "7", "30", or "all"
        db: Database session

    Returns:
        SQLAlchemy filter expression
    """
    if days == "all":
        return True  # No date filter
    elif days == "7":
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        return ContentQueueJob.created_at >= cutoff
    elif days == "30":
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        return ContentQueueJob.created_at >= cutoff
    else:
        raise ValueError(f"Invalid days parameter: {days}. Must be '7', '30', or 'all'")


def _classify_error(error_type: Optional[str], error_message: Optional[str]) -> str:
    """
    Classify error as transient, permanent, or unknown.

    Uses existing _is_transient() logic from queue_processor.py.

    Args:
        error_type: The last_error_type field
        error_message: The error_message field

    Returns:
        "transient", "permanent", or "unknown"
    """
    # Check error type first
    if error_type:
        if error_type in _TRANSIENT_ERROR_TYPES:
            return "transient"
        if error_type in _PERMANENT_ERROR_TYPES:
            return "permanent"

    # Check error message for transient keywords
    if error_message:
        msg_lower = error_message.lower()
        if any(keyword in msg_lower for keyword in _TRANSIENT_KEYWORDS):
            return "transient"

    # Default to unknown
    return "unknown"


def _directory_size_gb(directory: Path) -> float:
    """
    Calculate directory size in GB.

    Args:
        directory: Path to directory

    Returns:
        Size in GB
    """
    if not directory.exists():
        return 0.0

    total_bytes = 0
    try:
        for item in directory.rglob("*"):
            if item.is_file():
                total_bytes += item.stat().st_size
    except Exception as e:
        logger.warning("Failed to calculate directory size for %s: %s", directory, e)

    return total_bytes / (1024**3)  # Convert to GB
