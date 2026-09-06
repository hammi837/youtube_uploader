"""
routers/video_generation.py — Phase 2D video generation endpoints.

POST   /api/video-generation/from-content/{project_id}  — create job
GET    /api/video-generation                             — list jobs
GET    /api/video-generation/{job_id}                   — job status/progress
GET    /api/video-generation/{job_id}/video             — stream MP4
GET    /api/video-generation/{job_id}/thumbnail         — stream thumbnail
GET    /api/video-generation/{job_id}/captions          — download SRT
DELETE /api/video-generation/{job_id}                   — cancel/delete job

Security:
  - Files served by job ID only — never by filesystem path.
  - No credentials, tokens, or secrets in any response.
  - Path traversal prevented.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from backend.content_models import ContentProject
from backend.db import get_db
from backend.tts_models import GeneratedAudio, AudioStatus
from backend.video_generation_models import (
    VideoGenerationJob,
    VideoGenerationRequest,
    VideoJobResponse,
    VideoJobStatus,
    job_to_response,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video-generation", tags=["video-generation"])


# ── POST /api/video-generation/from-content/{project_id} ─────────────────────

@router.post(
    "/from-content/{project_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=VideoJobResponse,
)
def create_video_job(
    project_id: str,
    body: VideoGenerationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> VideoJobResponse:
    """
    Create a video-generation job for an existing content project.

    Returns immediately with status=queued.
    Poll GET /api/video-generation/{job_id} for progress.
    """
    # Validate project exists and has a script
    project = db.query(ContentProject).filter(ContentProject.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Content project not found: {project_id}",
        )
    if not project.script:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This content project has no generated script. Generate a script first.",
        )

    # Validate audio_id if provided
    audio_id = body.audio_id
    if audio_id:
        audio = db.query(GeneratedAudio).filter(
            GeneratedAudio.id == audio_id,
            GeneratedAudio.status == AudioStatus.COMPLETED,
        ).first()
        if not audio:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Completed audio record not found: {audio_id}",
            )

    # Create job record
    job = VideoGenerationJob(
        id=str(uuid.uuid4()),
        content_project_id=project_id,
        audio_id=audio_id,
        status=VideoJobStatus.QUEUED,
        progress=0,
        current_step="Queued",
        width=body.width,
        height=body.height,
        fps=body.fps,
        captions_enabled=body.captions_enabled,
        music_enabled=body.music_enabled,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(
        _run_video_pipeline_task,
        job_id=job.id,
        content_project_id=project_id,
        audio_id=audio_id,
        width=body.width,
        height=body.height,
        fps=body.fps,
        captions_enabled=body.captions_enabled,
        music_enabled=body.music_enabled,
    )

    logger.info(
        "Video generation job created: job_id=%s project=%s",
        job.id, project_id,
    )
    return job_to_response(job)


# ── GET /api/video-generation ─────────────────────────────────────────────────

@router.get("", response_model=list[VideoJobResponse])
def list_video_jobs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[VideoJobResponse]:
    """List recent video generation jobs, newest first."""
    jobs = (
        db.query(VideoGenerationJob)
        .order_by(VideoGenerationJob.created_at.desc())
        .limit(limit)
        .all()
    )
    return [job_to_response(j) for j in jobs]


# ── GET /api/video-generation/{job_id} ───────────────────────────────────────

@router.get("/{job_id}", response_model=VideoJobResponse)
def get_video_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> VideoJobResponse:
    """Get the current status and progress of a video generation job."""
    job = _get_job_or_404(job_id, db)
    return job_to_response(job)


# ── GET /api/video-generation/{job_id}/video ─────────────────────────────────

@router.get("/{job_id}/video")
def stream_video(
    job_id: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream the completed MP4 file. Files served by ID only."""
    job = _get_job_or_404(job_id, db)

    if job.status != VideoJobStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Video is not ready. Current status: {job.status}",
        )
    if not job.output_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video file path not recorded.",
        )

    file_path = Path(job.output_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video file not found on disk.",
        )

    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=f"video_{job_id}.mp4",
    )


# ── GET /api/video-generation/{job_id}/thumbnail ─────────────────────────────

@router.get("/{job_id}/thumbnail")
def stream_thumbnail(
    job_id: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream the generated thumbnail JPG."""
    job = _get_job_or_404(job_id, db)

    if not job.thumbnail_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No thumbnail available for this job.",
        )

    file_path = Path(job.thumbnail_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thumbnail file not found on disk.",
        )

    return FileResponse(
        path=str(file_path),
        media_type="image/jpeg",
        filename=f"thumbnail_{job_id}.jpg",
    )


# ── GET /api/video-generation/{job_id}/captions ──────────────────────────────

@router.get("/{job_id}/captions")
def download_captions(
    job_id: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Download the SRT caption file."""
    job = _get_job_or_404(job_id, db)

    if not job.caption_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No captions available for this job.",
        )

    file_path = Path(job.caption_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Caption file not found on disk.",
        )

    return FileResponse(
        path=str(file_path),
        media_type="text/plain",
        filename=f"captions_{job_id}.srt",
    )


# ── DELETE /api/video-generation/{job_id} ────────────────────────────────────

@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_video_job(
    job_id: str,
    db: Session = Depends(get_db),
):
    """Delete a job record and its associated output files."""
    job = _get_job_or_404(job_id, db)

    # Delete output files
    for attr in ("output_path", "thumbnail_path", "caption_path"):
        path_str = getattr(job, attr, None)
        if path_str:
            try:
                p = Path(path_str)
                if p.exists():
                    p.unlink()
            except OSError as exc:
                logger.warning("Could not delete %s: %s", path_str, exc)

    # Clean temp dir if it still exists
    from backend.services.video.media_utils import cleanup_temp
    cleanup_temp(job_id, keep_on_failure=False)

    db.delete(job)
    db.commit()


# ── Background task ───────────────────────────────────────────────────────────

def _run_video_pipeline_task(
    job_id: str,
    content_project_id: str,
    audio_id: Optional[str],
    width: int,
    height: int,
    fps: int,
    captions_enabled: bool,
    music_enabled: bool,
) -> None:
    """
    Background task: run the pipeline and update the DB record.

    Runs in a separate thread via FastAPI BackgroundTasks.
    Uses its own event loop (asyncio.run).
    """
    import asyncio
    from backend.db import SessionLocal
    from backend.services.video.exceptions import VideoGenerationError

    db = SessionLocal()

    def _update_progress(pct: int, step: str) -> None:
        """Thread-safe progress update."""
        try:
            j = db.query(VideoGenerationJob).filter(
                VideoGenerationJob.id == job_id
            ).first()
            if j:
                j.progress = pct
                j.current_step = step
                if pct >= 10 and j.status == VideoJobStatus.QUEUED:
                    j.status = VideoJobStatus.PREPARING
                elif pct >= 15 and j.status == VideoJobStatus.PREPARING:
                    j.status = VideoJobStatus.PREPARING_VISUALS
                elif pct >= 35 and j.status == VideoJobStatus.PREPARING_VISUALS:
                    j.status = VideoJobStatus.GENERATING_CAPTIONS
                elif pct >= 50 and j.status == VideoJobStatus.GENERATING_CAPTIONS:
                    j.status = VideoJobStatus.ASSEMBLING
                elif pct >= 94 and j.status == VideoJobStatus.ASSEMBLING:
                    j.status = VideoJobStatus.GENERATING_THUMBNAIL
                db.commit()
        except Exception as exc:
            logger.warning("Progress update failed: %s", exc)

    try:
        # Mark as preparing
        job = db.query(VideoGenerationJob).filter(
            VideoGenerationJob.id == job_id
        ).first()
        if not job:
            return
        job.status = VideoJobStatus.PREPARING
        job.current_step = "Starting pipeline..."
        db.commit()

        # Run the async pipeline
        result = asyncio.run(
            __import__(
                "backend.services.video.pipeline",
                fromlist=["run_pipeline"]
            ).run_pipeline(
                job_id=job_id,
                content_project_id=content_project_id,
                audio_id=audio_id,
                width=width,
                height=height,
                fps=fps,
                captions_enabled=captions_enabled,
                music_enabled=music_enabled,
                progress_callback=_update_progress,
            )
        )

        # Mark completed
        job = db.query(VideoGenerationJob).filter(
            VideoGenerationJob.id == job_id
        ).first()
        if job:
            job.status = VideoJobStatus.COMPLETED
            job.progress = 100
            job.current_step = "Complete"
            job.output_path    = result.get("output_path")
            job.thumbnail_path = result.get("thumbnail_path")
            job.caption_path   = result.get("caption_path")
            job.duration_seconds = result.get("duration_seconds")
            job.file_size_bytes  = result.get("file_size_bytes")
            job.completed_at = datetime.now(timezone.utc)
            db.commit()

        logger.info("Video pipeline completed: job_id=%s", job_id)

    except Exception as exc:
        logger.exception("Video pipeline failed: job_id=%s", job_id)
        try:
            job = db.query(VideoGenerationJob).filter(
                VideoGenerationJob.id == job_id
            ).first()
            if job:
                job.status = VideoJobStatus.FAILED
                job.error_message = str(exc)[:500]
                job.current_step = "Failed"
                db.commit()
            # Keep temp files on failure for debugging
            from backend.services.video.media_utils import cleanup_temp
            cleanup_temp(job_id, keep_on_failure=True)
        except Exception:
            pass
    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_job_or_404(job_id: str, db: Session) -> VideoGenerationJob:
    job = db.query(VideoGenerationJob).filter(
        VideoGenerationJob.id == job_id
    ).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video generation job not found: {job_id}",
        )
    return job
