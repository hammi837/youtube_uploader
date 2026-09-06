"""
routers/uploads.py — Upload job endpoints.

POST   /api/uploads                    — start a new upload job
GET    /api/uploads/{job_id}/status    — poll job progress
DELETE /api/uploads/{job_id}           — delete job record (not the YouTube video)
"""

from __future__ import annotations

import os
import uuid
import threading
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import (
    JobStatus,
    JobStatusResponse,
    UploadJob,
)
from backend.services import scheduler as sched_svc
from backend.services import youtube as yt_svc

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# Temp directory for uploaded files while the upload to YouTube is in progress
UPLOAD_TEMP_DIR = os.getenv("UPLOAD_TEMP_DIR", "./upload_tmp")


# ── POST /api/uploads ──────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobStatusResponse)
async def create_upload(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    # --- video file ---
    file: UploadFile = File(..., description="Video file to upload"),
    # --- metadata (Form fields so we can receive multipart/form-data) ---
    title: str              = Form(...,  max_length=100),
    description: str        = Form("",  max_length=5000),
    tags: str               = Form("",  description="Comma-separated tags"),
    category_id: str        = Form("22"),
    privacy_status: str     = Form("private"),
    scheduled_at: Optional[str]  = Form(None),
    timezone_name: Optional[str] = Form(None, alias="timezone"),
) -> JobStatusResponse:
    """
    Accept a video file and metadata, save the file temporarily, create a job
    record, and start the YouTube upload in a background thread.

    Returns immediately with a job_id for polling.
    """
    # Validate privacy
    if privacy_status not in ("private", "unlisted", "public"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="privacy_status must be 'private', 'unlisted', or 'public'",
        )

    # Parse and validate scheduled_at if provided
    publish_at = None
    if scheduled_at:
        try:
            if timezone_name:
                publish_at = sched_svc.parse_schedule_time_with_zone(scheduled_at, timezone_name)
            else:
                publish_at = sched_svc.parse_schedule_time(scheduled_at)
        except (ValueError, Exception) as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid scheduled_at: {e}",
            )

    # Save uploaded file to temp directory
    os.makedirs(UPLOAD_TEMP_DIR, exist_ok=True)
    ext       = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    temp_name = f"{uuid.uuid4()}{ext}"
    temp_path = os.path.join(UPLOAD_TEMP_DIR, temp_name)

    contents = await file.read()
    with open(temp_path, "wb") as f:
        f.write(contents)

    # Build tag string for DB storage
    tag_list   = [t.strip() for t in tags.split(",") if t.strip()]
    tags_str   = ",".join(tag_list)

    # Create job record
    job = UploadJob(
        id             = str(uuid.uuid4()),
        filename       = file.filename or temp_name,
        title          = title,
        description    = description,
        tags           = tags_str,
        category_id    = category_id,
        privacy_status = privacy_status,
        status         = JobStatus.PENDING,
        progress       = 0,
        scheduled_at   = publish_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_id = job.id

    # Run the upload in a background thread so we return immediately
    background_tasks.add_task(
        _run_upload_task,
        job_id    = job_id,
        temp_path = temp_path,
        publish_at= publish_at,
    )

    return JobStatusResponse.from_job(job)


# ── GET /api/uploads/{job_id}/status ──────────────────────────────────────────

@router.get("/{job_id}/status", response_model=JobStatusResponse)
def get_upload_status(
    job_id: str,
    db: Session = Depends(get_db),
) -> JobStatusResponse:
    """Poll the status and progress of an upload job."""
    job = _get_job_or_404(job_id, db)
    return JobStatusResponse.from_job(job)


# ── DELETE /api/uploads/{job_id} ──────────────────────────────────────────────

@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_upload_record(
    job_id: str,
    db: Session = Depends(get_db),
):
    """
    Delete the upload job record from the database.

    This does NOT delete the video from YouTube. Use the YouTube Studio
    or videos.delete API for that.
    """
    job = _get_job_or_404(job_id, db)
    db.delete(job)
    db.commit()


# ── Background task ────────────────────────────────────────────────────────────

def _run_upload_task(job_id: str, temp_path: str, publish_at) -> None:
    """
    Background task: runs the YouTube upload and updates the job record.

    Uses its own DB session (background tasks run outside the request session).
    """
    from backend.db import SessionLocal

    db = SessionLocal()
    try:
        job = db.query(UploadJob).filter(UploadJob.id == job_id).first()
        if not job:
            return

        try:
            yt_svc.run_upload(
                job        = job,
                file_path  = temp_path,
                publish_at = publish_at,
                db         = db,
            )
        except Exception as e:
            job.status        = JobStatus.FAILED
            job.error_message = str(e)
            db.commit()
        finally:
            # Clean up the temp file regardless of success/failure
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
    finally:
        db.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_job_or_404(job_id: str, db: Session) -> UploadJob:
    job = db.query(UploadJob).filter(UploadJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload job not found: {job_id}",
        )
    return job
