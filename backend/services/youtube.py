"""
backend/services/youtube.py — Thin async-friendly wrapper around the root youtube.py.

This module re-exports the core YouTube functions and adds helpers specific
to the FastAPI backend (e.g. building a progress_callback that writes to the DB).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy.orm import Session

# Root-level YouTube service
import youtube as yt_core
from backend.models import UploadJob, JobStatus


def make_progress_callback(job: UploadJob, db: Session) -> Callable[[int, int], None]:
    """
    Return a progress_callback that updates the UploadJob row in the database.

    Called by the upload background task after each chunk.
    """
    def callback(uploaded: int, total: int) -> None:
        if total > 0:
            job.progress = min(int(uploaded / total * 100), 99)
            job.status   = JobStatus.UPLOADING
            db.commit()

    return callback


def run_upload(
    job: UploadJob,
    file_path: str,
    publish_at: Optional[datetime],
    db: Session,
    chunk_size: int = yt_core.DEFAULT_CHUNK_SIZE,
) -> str:
    """
    Execute a YouTube upload for a given UploadJob.

    Updates job status/progress in the DB as the upload progresses.
    Returns the YouTube video_id on success.
    Raises on failure (caller is responsible for setting job.status = FAILED).
    """
    job.status   = JobStatus.UPLOADING
    job.progress = 0
    db.commit()

    callback = make_progress_callback(job, db)

    result = yt_core.upload_video(
        file_path      = file_path,
        title          = job.title,
        description    = job.description,
        tags           = job.tags_as_list(),
        category_id    = job.category_id,
        privacy_status = job.privacy_status,
        publish_at     = publish_at,
        chunk_size     = chunk_size,
        progress_callback = callback,
    )

    video_id      = result["video_id"]
    job.video_id  = video_id
    job.progress  = 100
    job.status    = JobStatus.SCHEDULED if publish_at else JobStatus.PUBLISHED
    db.commit()

    return video_id


# Re-export for convenience
update_video   = yt_core.update_video
set_thumbnail  = yt_core.set_thumbnail
build_service  = yt_core.build_service
