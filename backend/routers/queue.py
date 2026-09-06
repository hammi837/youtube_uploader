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
    ContentQueueJob,
    QueueJobResponse,
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

    processing = sum(
        counts.get(s, 0) for s in QueueStatus.ACTIVE
    )

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
        # Processing — we don't kill FFmpeg mid-run; mark for post-completion cancel.
        # For now, mark as failed with a clear message.
        job.status        = QueueStatus.CANCELLED
        job.current_stage = "Cancelled (was processing — will stop after current step)"
    else:
        job.status        = QueueStatus.CANCELLED
        job.current_stage = "Cancelled by user"

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
