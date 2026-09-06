"""
routers/videos.py — Video management endpoints.

GET    /api/videos                           — list all upload jobs
PATCH  /api/videos/{video_id}                — update metadata / reschedule / cancel schedule
POST   /api/videos/{video_id}/thumbnail      — upload a custom thumbnail
POST   /api/videos/{video_id}/cancel-schedule — cancel scheduled publishing
"""

from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import (
    JobStatusResponse,
    UpdateVideoRequest,
    UploadJob,
    VideoUpdateResponse,
)
from backend.services import scheduler as sched_svc
from backend.services import youtube as yt_svc

router = APIRouter(prefix="/api/videos", tags=["videos"])

UPLOAD_TEMP_DIR = os.getenv("UPLOAD_TEMP_DIR", "./upload_tmp")


# ── GET /api/videos ───────────────────────────────────────────────────────────

@router.get("", response_model=list[JobStatusResponse])
def list_videos(
    db: Session = Depends(get_db),
) -> list[JobStatusResponse]:
    """Return all upload jobs ordered by creation time (newest first)."""
    jobs = (
        db.query(UploadJob)
        .order_by(UploadJob.created_at.desc())
        .all()
    )
    return [JobStatusResponse.from_job(j) for j in jobs]


# ── PATCH /api/videos/{video_id} ─────────────────────────────────────────────

@router.patch("/{video_id}", response_model=VideoUpdateResponse)
def update_video(
    video_id: str,
    body: UpdateVideoRequest,
    db: Session = Depends(get_db),
) -> VideoUpdateResponse:
    """
    Update a video's metadata on YouTube.

    Supports:
      - Changing title, description, tags
      - Changing privacy status
      - Rescheduling (new scheduled_at)
      - Cancelling a schedule (clear_schedule=true → video stays private)

    Note: rescheduling only works if the video is still private and has
    never been published (YouTube API restriction).
    """
    publish_at = None

    if body.clear_schedule:
        # Cancel the schedule — keep private permanently
        pass
    elif body.scheduled_at is not None:
        try:
            if body.timezone:
                publish_at = sched_svc.parse_schedule_time_with_zone(
                    body.scheduled_at, body.timezone
                )
            else:
                publish_at = sched_svc.parse_schedule_time(body.scheduled_at)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid scheduled_at: {e}",
            )

    try:
        yt_svc.update_video(
            video_id       = video_id,
            title          = body.title,
            description    = body.description,
            tags           = body.tags,
            privacy_status = body.privacy_status,
            publish_at     = publish_at,
            clear_schedule = body.clear_schedule,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"YouTube API error: {e}",
        )

    # Also update the local DB record if we track this video
    job = db.query(UploadJob).filter(UploadJob.video_id == video_id).first()
    if job:
        if body.title:
            job.title = body.title
        if body.description is not None:
            job.description = body.description
        if body.tags is not None:
            job.tags = ",".join(body.tags)
        if body.privacy_status:
            job.privacy_status = body.privacy_status
        if body.clear_schedule:
            job.scheduled_at = None
        elif publish_at:
            job.scheduled_at = publish_at
        db.commit()

    return VideoUpdateResponse(video_id=video_id, message="Video updated successfully.")


# ── POST /api/videos/{video_id}/thumbnail ─────────────────────────────────────

@router.post("/{video_id}/thumbnail", response_model=VideoUpdateResponse)
async def upload_thumbnail(
    video_id: str,
    file: UploadFile = File(..., description="Thumbnail image (JPEG or PNG, max 2 MB, 1280×720 recommended)"),
    db: Session = Depends(get_db),
) -> VideoUpdateResponse:
    """
    Upload a custom thumbnail for an existing YouTube video.

    Requirements:
      - Channel must be verified to use custom thumbnails.
      - Image: JPEG or PNG, max 2 MB, 1280×720 recommended.
      - The video must already be uploaded (video_id must be valid).
    """
    # Validate file type
    content_type = file.content_type or ""
    if content_type not in ("image/jpeg", "image/png", "image/jpg"):
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in (".jpg", ".jpeg", ".png"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Thumbnail must be a JPEG or PNG image.",
            )

    # Save to temp file
    os.makedirs(UPLOAD_TEMP_DIR, exist_ok=True)
    ext       = os.path.splitext(file.filename or "thumb.jpg")[1] or ".jpg"
    temp_name = f"thumb_{uuid.uuid4()}{ext}"
    temp_path = os.path.join(UPLOAD_TEMP_DIR, temp_name)

    contents = await file.read()

    # Enforce 2 MB limit
    if len(contents) > 2 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Thumbnail file exceeds 2 MB limit.",
        )

    with open(temp_path, "wb") as f:
        f.write(contents)

    try:
        yt_svc.set_thumbnail(video_id=video_id, image_path=temp_path)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Temporary thumbnail file was not saved.")
    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg or "forbidden" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Thumbnail upload failed: channel not verified. "
                    "Custom thumbnails require a verified YouTube channel."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Thumbnail upload failed: {error_msg}",
        )
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass

    return VideoUpdateResponse(video_id=video_id, message="Thumbnail uploaded successfully.")


# ── POST /api/videos/{video_id}/cancel-schedule ──────────────────────────────

@router.post("/{video_id}/cancel-schedule", response_model=VideoUpdateResponse)
def cancel_schedule(
    video_id: str,
    db: Session = Depends(get_db),
) -> VideoUpdateResponse:
    """
    Cancel the scheduled publishing of a video, keeping it private permanently.

    Works only if the video is still private and has never been published.
    """
    try:
        yt_svc.update_video(
            video_id       = video_id,
            clear_schedule = True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"YouTube API error: {e}",
        )

    # Update local DB
    job = db.query(UploadJob).filter(UploadJob.video_id == video_id).first()
    if job:
        job.scheduled_at   = None
        job.privacy_status = "private"
        db.commit()

    return VideoUpdateResponse(
        video_id=video_id,
        message="Schedule cancelled. Video is now permanently private.",
    )
