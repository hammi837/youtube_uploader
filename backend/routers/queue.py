"""
routers/queue.py — Phase 3A content queue endpoints.

POST   /api/queue                     — bulk create queue jobs
GET    /api/queue                     — list jobs
GET    /api/queue/status              — queue stats + worker state
GET    /api/queue/{job_id}            — single job detail
POST   /api/queue/{job_id}/cancel     — cancel a queued job
POST   /api/queue/{job_id}/retry      — retry a failed job
POST   /api/queue/pause               — pause queue (current job finishes)
POST   /api/queue/resume              — resume paused queue
POST   /api/queue/start               — start the worker
DELETE /api/queue/{job_id}            — delete a job record

Security: no credentials, no tokens, no filesystem paths exposed.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.queue_models import (
    BulkQueueRequest,
    BulkQueueResponse,
    CleanupResponse,
    ContentQueueJob,
    QueueHealthResponse,
    QueueJobResponse,
    QueueStatsResponse,
    QueueStatus,
    QueueStatusResponse,
    queue_job_to_response,
)
from backend.services.queue_processor import (
    get_current_job_id,
    is_paused,
    is_worker_alive,
    pause_queue,
    resume_queue,
    start_worker,
    stop_worker,
)
from backend.services.queue_services import (
    find_duplicate_topics,
    get_job_logs,
    get_queue_health,
    get_queue_stats,
    get_uploads_today,
    get_upload_limit,
    check_disk_space,
    get_min_free_disk_gb,
    run_cleanup,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/queue", tags=["queue"])


# ── POST /api/queue ───────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, response_model=BulkQueueResponse)
def create_queue_jobs(
    body: BulkQueueRequest,
    db: Session = Depends(get_db),
) -> BulkQueueResponse:
    """
    Create one or more content queue jobs.

    Topics are processed sequentially, one at a time, in the order submitted.
    If schedule_start is provided, each job gets a scheduled_publish_at
    offset by schedule_interval_minutes × job_index.
    """
    from backend.services.scheduler import parse_schedule_time, parse_schedule_time_with_zone
    schedule_times: list[Optional[datetime]] = []
    schedule_summary: list[str] = []

    if body.schedule_start:
        try:
            if body.schedule_timezone:
                first_dt = parse_schedule_time_with_zone(
                    body.schedule_start, body.schedule_timezone
                )
            else:
                first_dt = parse_schedule_time(body.schedule_start)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid schedule_start: {exc}",
            )

        for i in range(len(body.topics)):
            dt = first_dt + timedelta(minutes=body.schedule_interval_minutes * i)
            schedule_times.append(dt)
            schedule_summary.append(
                f"Video {i+1} '{body.topics[i][:40]}': "
                f"{dt.strftime('%Y-%m-%d %H:%M UTC')}"
            )
    else:
        schedule_times = [None] * len(body.topics)
        schedule_summary = [f"Video {i+1} '{t[:40]}': no schedule" for i, t in enumerate(body.topics)]

    # ── Duplicate topic detection ──────────────────────────────────────────
    duplicates = find_duplicate_topics(body.topics)
    if duplicates:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The following topics already have pending/active queue jobs: "
                f"{', '.join(duplicates[:5])}. "
                "Cancel existing jobs first or use different topics."
            ),
        )

    # ── Create job records ─────────────────────────────────────────────────
    jobs: list[ContentQueueJob] = []
    for i, topic in enumerate(body.topics):
        job = ContentQueueJob(
            id=str(uuid.uuid4()),
            topic=topic,
            language=body.language,
            tone=body.tone,
            target_duration_seconds=body.target_duration_seconds,
            scene_count=body.scene_count,
            priority=body.priority,
            max_retries=body.max_retries,
            scheduled_publish_at=schedule_times[i],
            youtube_privacy_status=body.youtube_privacy_status,
            youtube_category_id=body.youtube_category_id,
            status=QueueStatus.QUEUED,
        )
        db.add(job)
        jobs.append(job)

    db.commit()
    for job in jobs:
        db.refresh(job)

    # ── Auto-start worker if not already running ───────────────────────────
    start_worker()

    logger.info(
        "Created %d queue jobs. Schedule: %s",
        len(jobs), "yes" if body.schedule_start else "no",
    )

    return BulkQueueResponse(
        created=len(jobs),
        jobs=[queue_job_to_response(j) for j in jobs],
        schedule_summary=schedule_summary,
    )


# ── GET /api/queue/status ─────────────────────────────────────────────────────

@router.get("/status", response_model=QueueStatusResponse)
def get_queue_status(db: Session = Depends(get_db)) -> QueueStatusResponse:
    """Return current queue worker state and job counts."""
    counts: dict[str, int] = {}
    for s in QueueStatus.ALL:
        counts[s] = db.query(ContentQueueJob).filter(
            ContentQueueJob.status == s
        ).count()

    processing = sum(counts.get(s, 0) for s in QueueStatus.ACTIVE)
    uploads_today = get_uploads_today()
    upload_limit  = get_upload_limit()
    _, free_gb    = check_disk_space()
    min_gb        = get_min_free_disk_gb()

    return QueueStatusResponse(
        queue_running=is_worker_alive(),
        queue_paused=is_paused(),
        current_job_id=get_current_job_id(),
        total=db.query(ContentQueueJob).count(),
        queued=counts.get(QueueStatus.QUEUED, 0),
        processing=processing,
        completed=counts.get(QueueStatus.COMPLETED, 0) + counts.get(QueueStatus.SCHEDULED, 0),
        failed=counts.get(QueueStatus.FAILED, 0),
        cancelled=counts.get(QueueStatus.CANCELLED, 0),
        paused=counts.get(QueueStatus.PAUSED, 0),
        uploads_today=uploads_today,
        upload_limit=upload_limit,
        uploads_remaining=max(0, upload_limit - uploads_today),
        free_disk_gb=free_gb,
        disk_warning=free_gb < min_gb,
    )


# ── GET /api/queue ────────────────────────────────────────────────────────────

@router.get("", response_model=list[QueueJobResponse])
def list_queue_jobs(
    limit: int = Query(50, ge=1, le=200),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
) -> list[QueueJobResponse]:
    """List queue jobs, newest first. Optionally filter by status."""
    q = db.query(ContentQueueJob)
    if status_filter:
        if status_filter not in QueueStatus.ALL:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status filter: '{status_filter}'",
            )
        q = q.filter(ContentQueueJob.status == status_filter)
    jobs = q.order_by(ContentQueueJob.created_at.desc()).limit(limit).all()
    return [queue_job_to_response(j) for j in jobs]


# ── GET /api/queue/health — MUST be before /{job_id} ─────────────────────────

@router.get("/health", response_model=QueueHealthResponse)
def get_queue_health_endpoint() -> QueueHealthResponse:
    """Return queue health: worker state, disk space, upload limit."""
    h = get_queue_health()
    return QueueHealthResponse(**h)


# ── GET /api/queue/stats — MUST be before /{job_id} ──────────────────────────

@router.get("/stats", response_model=QueueStatsResponse)
def get_queue_stats_endpoint() -> QueueStatsResponse:
    """Return detailed queue statistics including avg processing time."""
    return QueueStatsResponse(**get_queue_stats())


# ── GET /api/queue/{job_id} ───────────────────────────────────────────────────

@router.get("/{job_id}", response_model=QueueJobResponse)
def get_queue_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> QueueJobResponse:
    """Get detailed status for a single queue job."""
    job = _get_job_or_404(job_id, db)
    return queue_job_to_response(job)


# ── POST /api/queue/{job_id}/cancel ──────────────────────────────────────────

@router.post("/{job_id}/cancel", response_model=QueueJobResponse)
def cancel_queue_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> QueueJobResponse:
    """
    Cancel a queued or paused job.
    Currently processing jobs: sets cancelled after they finish.
    Completed/failed jobs cannot be cancelled.
    """
    job = _get_job_or_404(job_id, db)

    if job.status in QueueStatus.TERMINAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is already in terminal state: {job.status}",
        )

    if job_id == get_current_job_id():
        job.status        = QueueStatus.CANCELLED
        job.current_stage = "Cancelled (was processing — will stop after current step)"
        job.cancelled_at  = datetime.now(timezone.utc)
    else:
        job.status        = QueueStatus.CANCELLED
        job.current_stage = "Cancelled by user"
        job.cancelled_at  = datetime.now(timezone.utc)

    db.commit()
    db.refresh(job)
    return queue_job_to_response(job)


# ── POST /api/queue/{job_id}/retry ────────────────────────────────────────────

@router.post("/{job_id}/retry", response_model=QueueJobResponse)
def retry_queue_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> QueueJobResponse:
    """
    Reset a failed or cancelled job back to queued for retry.
    Also resets retry_count so it gets max_retries attempts again.
    """
    job = _get_job_or_404(job_id, db)

    if job.status not in (QueueStatus.FAILED, QueueStatus.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only failed or cancelled jobs can be retried. Current status: {job.status}",
        )

    job.status        = QueueStatus.QUEUED
    job.current_stage = "Requeued for retry"
    job.progress      = 0
    job.retry_count   = 0
    job.error_message = None
    db.commit()
    db.refresh(job)

    start_worker()  # ensure worker is running
    return queue_job_to_response(job)


# ── POST /api/queue/pause ─────────────────────────────────────────────────────

@router.post("/pause", status_code=status.HTTP_200_OK)
def pause_queue_endpoint():
    """Pause queue processing. Current job finishes; no new job starts."""
    pause_queue()
    return {"message": "Queue paused. Current job will finish before stopping."}


# ── POST /api/queue/resume ────────────────────────────────────────────────────

@router.post("/resume", status_code=status.HTTP_200_OK)
def resume_queue_endpoint():
    """Resume a paused queue."""
    resume_queue()
    start_worker()
    return {"message": "Queue resumed."}


# ── POST /api/queue/start ─────────────────────────────────────────────────────

@router.post("/start", status_code=status.HTTP_200_OK)
def start_queue_endpoint():
    """Explicitly start the queue worker (auto-starts on job creation too)."""
    started = start_worker()
    if started:
        return {"message": "Queue worker started."}
    return {"message": "Queue worker already running."}


# ── DELETE /api/queue/{job_id} ────────────────────────────────────────────────

@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_queue_job(
    job_id: str,
    db: Session = Depends(get_db),
):
    """
    Delete a queue job record.
    Does NOT delete generated videos or YouTube uploads.
    Only terminal-state jobs can be deleted.
    """
    job = _get_job_or_404(job_id, db)

    if job_id == get_current_job_id():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a currently processing job.",
        )
    if job.status not in QueueStatus.TERMINAL and job.status != QueueStatus.QUEUED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete job in status '{job.status}'. Cancel it first.",
        )

    db.delete(job)
    db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_job_or_404(job_id: str, db: Session) -> ContentQueueJob:
    job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Queue job not found: {job_id}",
        )
    return job


# ── POST /api/queue/stop ──────────────────────────────────────────────────────

@router.post("/stop", status_code=status.HTTP_200_OK)
def stop_queue_endpoint():
    """
    Gracefully stop the queue worker after the current job finishes.
    State is preserved in the database.
    """
    stop_worker()
    return {"message": "Queue worker stop requested. Current job will finish before stopping."}


# ── POST /api/queue/retry-failed ─────────────────────────────────────────────

@router.post("/retry-failed", status_code=status.HTTP_200_OK)
def retry_all_failed(db: Session = Depends(get_db)):
    """Reset ALL failed jobs back to queued. Returns count of jobs reset."""
    failed_jobs = (
        db.query(ContentQueueJob)
        .filter(ContentQueueJob.status == QueueStatus.FAILED)
        .all()
    )
    count = 0
    for job in failed_jobs:
        job.status        = QueueStatus.QUEUED
        job.current_stage = "Requeued for retry (bulk retry)"
        job.progress      = 0
        job.retry_count   = 0
        job.error_message = None
        count += 1
    db.commit()

    if count > 0:
        start_worker()

    return {"message": f"Reset {count} failed job(s) to queued.", "count": count}


# ── POST /api/queue/clear-completed ──────────────────────────────────────────

@router.post("/clear-completed", status_code=status.HTTP_200_OK)
def clear_completed_jobs(db: Session = Depends(get_db)):
    """Delete all completed queue job records (NOT the generated files)."""
    completed = (
        db.query(ContentQueueJob)
        .filter(ContentQueueJob.status == QueueStatus.COMPLETED)
        .all()
    )
    count = len(completed)
    for job in completed:
        db.delete(job)
    db.commit()
    return {"message": f"Deleted {count} completed job record(s).", "count": count}


# ── POST /api/queue/clear-failed ─────────────────────────────────────────────

@router.post("/clear-failed", status_code=status.HTTP_200_OK)
def clear_failed_jobs(db: Session = Depends(get_db)):
    """Delete all failed/cancelled queue job records."""
    jobs = (
        db.query(ContentQueueJob)
        .filter(ContentQueueJob.status.in_([QueueStatus.FAILED, QueueStatus.CANCELLED]))
        .all()
    )
    count = len(jobs)
    for job in jobs:
        db.delete(job)
    db.commit()
    return {"message": f"Deleted {count} failed/cancelled job record(s).", "count": count}


# ── POST /api/queue/cleanup ───────────────────────────────────────────────────

@router.post("/cleanup", response_model=CleanupResponse)
def run_cleanup_endpoint(
    dry_run: bool = False,
    db: Session = Depends(get_db),
) -> CleanupResponse:
    """
    Clean up old generated media files.
    Use ?dry_run=true to preview without deleting.
    """
    result = run_cleanup(dry_run=dry_run)
    return CleanupResponse(
        files_deleted=result["files_deleted"],
        bytes_freed=result["bytes_freed"],
        files_skipped=result["files_skipped"],
        errors=result["errors"],
    )


# ── GET /api/queue/{job_id}/logs ──────────────────────────────────────────────

@router.get("/{job_id}/logs")
def get_job_logs_endpoint(
    job_id: str,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return chronological log entries for a job. No credentials exposed."""
    _get_job_or_404(job_id, db)
    return get_job_logs(job_id, limit=limit)
