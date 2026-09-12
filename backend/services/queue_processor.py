"""
backend/services/queue_processor.py — Phase 3A/3B content queue worker.

Orchestrates the full pipeline for each queue job:
    Research → Script → TTS → Video → Thumbnail → YouTube Upload → Schedule

Design:
  - SEQUENTIAL: only ONE job runs at a time (i5-6300U has only 2 cores).
  - Database-backed state: survives backend restart.
  - Duplicate upload protection: youtube_video_id guards against re-upload.
  - Pause/resume: respected at the boundary between jobs, not mid-job.
  - Retry: transient errors retry up to max_retries with configurable delays.
  - Recovery: stale "active" jobs on startup are detected and requeued/failed.
  - Phase 3B: per-job logging, disk-space check, daily upload limit.
  - All generated media stays on G: — never C:.

This module ONLY orchestrates existing services. It does not re-implement
research, TTS, video generation, or YouTube upload logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Optional

logger = logging.getLogger(__name__)

# ── Module-level worker state ─────────────────────────────────────────────────
# These are module-level so they survive across requests within one process.

_worker_thread: Optional[Thread] = None
_stop_event  = Event()   # set to request graceful stop of the worker loop
_queue_paused: bool = False
_current_job_id: Optional[str] = None


# ── Public control API ────────────────────────────────────────────────────────

def start_worker() -> bool:
    """
    Start the background queue worker thread if not already running.
    Returns True if started, False if already running.
    """
    global _worker_thread, _stop_event

    if _worker_thread is not None and _worker_thread.is_alive():
        logger.info("Queue worker already running.")
        return False

    _stop_event.clear()
    _worker_thread = Thread(
        target=_worker_loop,
        name="queue-worker",
        daemon=True,
    )
    _worker_thread.start()
    logger.info("Queue worker started.")
    return True


def stop_worker() -> None:
    """Request the worker to stop after its current job finishes."""
    global _stop_event
    _stop_event.set()
    logger.info("Queue worker stop requested.")


def pause_queue() -> None:
    global _queue_paused
    _queue_paused = True
    logger.info("Queue paused (current job will finish).")


def resume_queue() -> None:
    global _queue_paused
    _queue_paused = False
    logger.info("Queue resumed.")


def is_worker_alive() -> bool:
    return _worker_thread is not None and _worker_thread.is_alive()


def is_paused() -> bool:
    return _queue_paused


def get_current_job_id() -> Optional[str]:
    return _current_job_id


# ── Worker loop ───────────────────────────────────────────────────────────────

def _worker_loop() -> None:
    """
    Main loop: repeatedly picks the next queued job and processes it.
    Runs in its own daemon thread.
    """
    logger.info("Queue worker loop started.")

    # Safety: recover any stale jobs from a previous crash
    _recover_stale_jobs()

    while not _stop_event.is_set():
        if _queue_paused:
            time.sleep(3)
            continue

        job_id = _claim_next_job()
        if job_id is None:
            # No jobs waiting — sleep and poll
            time.sleep(5)
            continue

        _process_job(job_id)
        # DDG rate-limit protection: pause 15s between consecutive jobs
        # so the search provider doesn't see rapid sequential requests.
        if not _stop_event.is_set():
            time.sleep(15)

    logger.info("Queue worker loop exited.")


def _claim_next_job() -> Optional[str]:
    """
    Atomically pick the highest-priority queued job and mark it as active.
    Respects next_retry_at — skips jobs whose retry window hasn't elapsed.
    Returns the job ID, or None if the queue is empty.
    """
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob, QueueStatus

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        job = (
            db.query(ContentQueueJob)
            .filter(
                ContentQueueJob.status == QueueStatus.QUEUED,
                # Only claim if next_retry_at is null or in the past
                (ContentQueueJob.next_retry_at.is_(None) | (ContentQueueJob.next_retry_at <= now)),
            )
            .order_by(
                ContentQueueJob.priority.desc(),
                ContentQueueJob.created_at.asc(),
            )
            .first()
        )
        if job is None:
            return None

        job.status = QueueStatus.RESEARCHING
        job.current_stage = "Starting…"
        job.started_at = now
        job.next_retry_at = None  # clear retry timer on claim
        db.commit()
        return job.id
    except Exception as exc:
        logger.error("Failed to claim next job: %s", exc)
        return None
    finally:
        db.close()


def _process_job(job_id: str) -> None:
    """Run the complete pipeline for one queue job."""
    global _current_job_id
    _current_job_id = job_id
    logger.info("Processing queue job: %s", job_id)
    t_start = time.perf_counter()

    try:
        _run_full_pipeline(job_id)
        elapsed = time.perf_counter() - t_start
        logger.info("Queue job completed: %s (%.1fs)", job_id, elapsed)
    except Exception as exc:
        logger.exception("Queue job failed unexpectedly: %s", job_id)
        _mark_failed(job_id, f"Unexpected error: {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        _current_job_id = None


# ── Pipeline stages ────────────────────────────────────────────────────────────

def _run_full_pipeline(job_id: str) -> None:
    """
    Orchestrate all pipeline stages for one job.
    Each stage updates the job status, current_stage, and progress in the DB.
    """
    from backend.queue_models import QueueStatus
    from backend.services.queue_services import (
        log_job, check_disk_space, check_upload_limit,
        get_min_free_disk_gb, increment_uploads_today,
    )

    log_job(job_id, "Pipeline started.", stage="init")

    # ── Disk-space check ───────────────────────────────────────────────────
    disk_ok, free_gb = check_disk_space()
    if not disk_ok:
        min_gb = get_min_free_disk_gb()
        msg = (
            f"Queue paused: only {free_gb:.1f} GB free on G:. "
            f"Minimum required is {min_gb} GB."
        )
        log_job(job_id, msg, level="warning", stage="disk_check")
        from backend.db import SessionLocal
        from backend.queue_models import ContentQueueJob
        db = SessionLocal()
        try:
            job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
            if job:
                job.status = QueueStatus.QUEUED  # put back in queue
                job.current_stage = msg
                db.commit()
        finally:
            db.close()
        pause_queue()
        raise RuntimeError(msg)

    # ── Stage 1: Research + Script (5–15%) ────────────────────────────────
    _update_job(job_id, status=QueueStatus.RESEARCHING, stage="Researching topic…", progress=2)
    log_job(job_id, "Research started.", stage="research")
    content_project_id = _stage_research_and_script(job_id)
    log_job(job_id, f"Script generated: project_id={content_project_id}", stage="research")

    # ── Stage 2: TTS narration (15–30%) ───────────────────────────────────
    _update_job(job_id, status=QueueStatus.GENERATING_AUDIO, stage="Generating narration…", progress=15)
    log_job(job_id, "TTS narration started.", stage="tts")
    audio_id = _stage_generate_audio(job_id, content_project_id)
    log_job(job_id, f"TTS completed: audio_id={audio_id}", stage="tts")

    # ── Stage 3: Video generation (30–87%) ────────────────────────────────
    _update_job(job_id, status=QueueStatus.GENERATING_VIDEO, stage="Generating video…", progress=30)
    log_job(job_id, "Video generation started.", stage="video")
    video_job_id = _stage_generate_video(job_id, content_project_id, audio_id)
    log_job(job_id, f"Video generated: video_job_id={video_job_id}", stage="video")

    # ── Upload limit check before YouTube stage ────────────────────────────
    upload_ok, uploads_today, upload_limit = check_upload_limit()
    if not upload_ok:
        msg = (
            f"Daily upload limit reached ({uploads_today}/{upload_limit}). "
            "Job will retry when the limit resets (UTC midnight)."
        )
        log_job(job_id, msg, level="warning", stage="youtube")
        raise RuntimeError(msg)

    # ── Stage 4: YouTube upload + thumbnail + schedule (87–100%) ──────────
    _update_job(job_id, status=QueueStatus.UPLOADING, stage="Uploading to YouTube…", progress=87)
    log_job(job_id, "YouTube upload started.", stage="youtube")
    _stage_youtube(job_id, video_job_id)
    increment_uploads_today()
    log_job(job_id, "YouTube upload complete.", stage="youtube")

    # ── Done ───────────────────────────────────────────────────────────────
    _update_job(
        job_id,
        status=QueueStatus.COMPLETED,
        stage="Complete",
        progress=100,
        completed_at=datetime.now(timezone.utc),
    )
    log_job(job_id, "Job completed successfully.", stage="complete")


# ── Stage implementations ──────────────────────────────────────────────────────

def _stage_research_and_script(job_id: str) -> str:
    """
    Run research + script generation using existing Phase 2A services.
    Returns content_project_id.
    """
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob, QueueStatus
    from backend.content_models import (
        ContentProject, ContentStatus, GeneratedScriptRecord,
        ResearchSourceRecord, ScriptRequest,
    )
    from backend.services.script_generator import generate_script
    import json as _json

    db = SessionLocal()
    try:
        job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
        if not job:
            raise RuntimeError(f"Job not found: {job_id}")

        # If we already have a project from a previous attempt, reuse it
        if job.content_project_id:
            project = db.query(ContentProject).filter(
                ContentProject.id == job.content_project_id,
                ContentProject.status == ContentStatus.COMPLETED,
            ).first()
            if project and project.script:
                logger.info("Reusing existing content project: %s", job.content_project_id)
                return job.content_project_id

        request = ScriptRequest(
            topic=_sanitise_topic(job.topic),
            language=job.language,
            tone=job.tone,
            target_duration_seconds=job.target_duration_seconds,
            scene_count=job.scene_count,
        )

        _update_job(job_id, stage="Researching…", progress=3)
        research, script = _retry_call(
            fn=lambda: generate_script(request),
            job_id=job_id,
            stage_name="research+script",
        )

        _update_job(job_id, stage="Saving script…", progress=12)

        # Persist project + sources + script (same as routers/content.py)
        project = ContentProject(
            id=str(uuid.uuid4()),
            topic=job.topic,
            language=job.language,
            tone=job.tone,
            target_duration_seconds=job.target_duration_seconds,
            scene_count=job.scene_count,
            status=ContentStatus.COMPLETED,
        )
        db.add(project)

        for src in research.sources:
            db.add(ResearchSourceRecord(
                id=str(uuid.uuid4()),
                content_project_id=project.id,
                title=src.title,
                url=src.url,
                snippet=src.snippet,
                source_data=_json.dumps(src.key_points),
            ))

        db.add(GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title=script.title,
            description=script.description,
            hook=script.hook,
            tags_json=_json.dumps(script.tags),
            scenes_json=_json.dumps([s.model_dump() for s in script.scenes]),
            estimated_duration_seconds=script.estimated_duration_seconds,
        ))

        # Link job to project
        job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
        job.content_project_id = project.id
        db.commit()

        logger.info(
            "Script generated: project_id=%s title='%s'",
            project.id, script.title,
        )
        return project.id

    finally:
        db.close()


def _stage_generate_audio(job_id: str, content_project_id: str) -> Optional[str]:
    """
    Generate TTS narration using existing Phase 2B/2C services.
    Returns audio_id (or None if failed — video pipeline handles missing audio).
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject
    from backend.tts_models import GeneratedAudio, AudioStatus
    from backend.services.tts.factory import get_tts_provider
    from backend.services.tts.narration import extract_narration_text
    from backend.services.video.media_utils import get_data_dir
    import os as _os

    db = SessionLocal()
    try:
        # Check for existing completed audio
        existing = (
            db.query(GeneratedAudio)
            .filter(
                GeneratedAudio.content_project_id == content_project_id,
                GeneratedAudio.status == AudioStatus.COMPLETED,
            )
            .order_by(GeneratedAudio.created_at.desc())
            .first()
        )
        if existing and existing.file_path and Path(existing.file_path).exists():
            logger.info("Reusing existing audio: %s", existing.id)
            return existing.id

        project = db.query(ContentProject).filter(
            ContentProject.id == content_project_id
        ).first()
        if not project or not project.script:
            raise RuntimeError("Content project or script not found for TTS.")

        narration_text = extract_narration_text(project.script)
        voice = _os.getenv("TTS_DEFAULT_VOICE", "en-US-AriaNeural")

        output_dir = get_data_dir() / "audio"
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_id   = str(uuid.uuid4())
        output_path = str(output_dir / f"{audio_id}.wav")

        _update_job(job_id, stage="Synthesizing narration…", progress=18)

        def _do_tts():
            from backend.services.queue_services import log_job as _log
            provider = get_tts_provider()
            provider_name = provider.provider_name if hasattr(provider, 'provider_name') else 'unknown'
            _log(job_id, f"TTS provider selected: {provider_name}", stage="tts")
            _log(job_id, "TTS synthesis starting…", stage="tts")

            # Run the async synthesize() in a fresh dedicated event loop.
            # The queue worker runs in a plain thread (not inside asyncio.run),
            # so there is no running loop. We create one, run the coroutine,
            # and close it cleanly. Do NOT call asyncio.set_event_loop() as
            # that modifies global state and can interfere with other threads.
            loop = asyncio.new_event_loop()
            try:
                _log(job_id, f"TTS event loop created, calling synthesize() with provider={provider_name}", stage="tts")
                result = loop.run_until_complete(
                    provider.synthesize(narration_text, voice, output_path)
                )
                _log(job_id, f"TTS synthesize() returned successfully", stage="tts")
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
                loop.close()
                _log(job_id, "TTS event loop closed", stage="tts")

            used_provider = (
                provider.last_used_provider
                if hasattr(provider, "last_used_provider")
                else provider.provider_name
            )
            _log(job_id, f"TTS synthesis complete: provider={used_provider} file={Path(result.file_path).name} size={result.file_size_bytes} bytes", stage="tts")
            return result, used_provider

        result, used_provider = _retry_call(
            fn=_do_tts,
            job_id=job_id,
            stage_name="TTS",
        )

        audio_record = GeneratedAudio(
            id=audio_id,
            content_project_id=content_project_id,
            voice=voice,
            language=project.language or "en",
            text_length=len(narration_text),
            file_path=result.file_path,
            file_size_bytes=result.file_size_bytes,
            duration_seconds=result.duration_seconds,
            status=AudioStatus.COMPLETED,
            provider=used_provider,
        )
        db.add(audio_record)
        db.commit()

        logger.info("Audio generated: %s provider=%s", audio_id, used_provider)
        _update_job(job_id, stage="Narration ready.", progress=28)
        return audio_id

    except Exception as exc:
        logger.error("TTS stage failed: %s — job will be marked as failed.", exc)
        # Re-raise to let _retry_call handle the failure with proper retry logic
        # This ensures the job is marked as failed instead of continuing without audio
        raise


def _stage_generate_video(
    job_id: str,
    content_project_id: str,
    audio_id: Optional[str],
) -> str:
    """
    Generate the video using existing Phase 2D pipeline.
    Returns video_job_id.
    """
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob
    from backend.video_generation_models import VideoGenerationJob, VideoJobStatus
    import os as _os

    def _progress_cb(pct: int, step: str) -> None:
        # Map video pipeline 0-100 into overall progress 30-87
        mapped = 30 + int(pct * 0.57)
        _update_job(job_id, stage=f"Video: {step}", progress=mapped)

    video_job_id_ref: list[str] = []

    def _do_video():
        from backend.services.video.pipeline import run_pipeline

        vj_id = str(uuid.uuid4())
        video_job_id_ref.append(vj_id)

        # Create VideoGenerationJob record
        db = SessionLocal()
        try:
            vj = VideoGenerationJob(
                id=vj_id,
                content_project_id=content_project_id,
                audio_id=audio_id,
                status=VideoJobStatus.PREPARING,
                progress=0,
                current_step="Starting…",
                width=1920,
                height=1080,
                fps=30,
                captions_enabled=True,
                music_enabled=True,
            )
            db.add(vj)

            # Link queue job to video job early
            qj = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
            if qj:
                qj.video_job_id = vj_id
            db.commit()
        finally:
            db.close()

        result = asyncio.run(run_pipeline(
            job_id=vj_id,
            content_project_id=content_project_id,
            audio_id=audio_id,
            width=1920,
            height=1080,
            fps=30,
            captions_enabled=True,
            music_enabled=True,
            progress_callback=_progress_cb,
        ))

        # Update VideoGenerationJob record
        db2 = SessionLocal()
        try:
            vj2 = db2.query(VideoGenerationJob).filter(VideoGenerationJob.id == vj_id).first()
            if vj2:
                vj2.status        = VideoJobStatus.COMPLETED
                vj2.progress      = 100
                vj2.current_step  = "Complete"
                vj2.output_path   = result.get("output_path")
                vj2.thumbnail_path = result.get("thumbnail_path")
                vj2.caption_path  = result.get("caption_path")
                vj2.duration_seconds = result.get("duration_seconds")
                vj2.file_size_bytes  = result.get("file_size_bytes")
                vj2.completed_at  = datetime.now(timezone.utc)
                db2.commit()
        finally:
            db2.close()

        return result

    _retry_call(fn=_do_video, job_id=job_id, stage_name="video_generation")

    return video_job_id_ref[0] if video_job_id_ref else ""


def _stage_youtube(job_id: str, video_job_id: str) -> None:
    """
    Upload video + thumbnail to YouTube, then set schedule if configured.
    Updates individual sub-status flags.
    """
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob, QueueStatus
    from backend.video_generation_models import VideoGenerationJob
    from backend.content_models import ContentProject, GeneratedScriptRecord

    db = SessionLocal()
    try:
        job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
        if not job:
            raise RuntimeError(f"Queue job not found: {job_id}")

        # ── Duplicate protection ───────────────────────────────────────────
        if job.youtube_video_id:
            logger.info(
                "Skipping upload — youtube_video_id already set: %s",
                job.youtube_video_id,
            )
            _update_job(job_id, stage="Already uploaded.", progress=95)
            return

        vj = db.query(VideoGenerationJob).filter(
            VideoGenerationJob.id == video_job_id
        ).first()
        if not vj or not vj.output_path:
            raise RuntimeError("Video generation job has no output file.")

        output_path = vj.output_path
        if not Path(output_path).exists():
            raise RuntimeError(f"Video file not found: {output_path}")

        # Get title / description from content project
        title = job.topic
        description = ""
        tags: list[str] = []
        if job.content_project_id:
            cp = db.query(ContentProject).filter(
                ContentProject.id == job.content_project_id
            ).first()
            if cp and cp.script:
                title       = cp.script.title or job.topic
                description = cp.script.description or ""
                tags        = cp.script.tags_as_list()

        # ── Upload video ───────────────────────────────────────────────────
        _update_job(job_id, stage="Uploading video to YouTube…", progress=88)

        import youtube as yt_core
        from googleapiclient.errors import HttpError

        logger.info("Uploading video: %s -> '%s'", Path(output_path).name, title[:60])

        upload_result = _retry_call(
            fn=lambda: yt_core.upload_video(
                file_path=output_path,
                title=title[:100],
                description=description[:5000],
                tags=tags[:30],
                category_id=job.youtube_category_id,
                privacy_status="private",  # always private initially
                publish_at=None,           # set schedule separately
            ),
            job_id=job_id,
            stage_name="youtube_upload",
            permanent_exceptions=(FileNotFoundError, ValueError),
        )

        video_id = upload_result["video_id"]
        logger.info("YouTube upload complete: video_id=%s", video_id)

        # Store video_id immediately to prevent duplicate upload on retry
        job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
        job.youtube_video_id = video_id
        job.youtube_url      = f"https://www.youtube.com/watch?v={video_id}"
        job.video_uploaded   = True
        db.commit()

        # ── Upload thumbnail ───────────────────────────────────────────────
        if vj.thumbnail_path and Path(vj.thumbnail_path).exists():
            _update_job(job_id, stage="Uploading thumbnail…", progress=93)
            try:
                yt_core.set_thumbnail(video_id=video_id, image_path=vj.thumbnail_path)
                job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
                job.thumbnail_uploaded = True
                db.commit()
                logger.info("Thumbnail uploaded for video_id=%s", video_id)
            except Exception as exc:
                # Thumbnail failure is recorded but does NOT fail the whole job
                logger.warning("Thumbnail upload failed for %s: %s", video_id, exc)
                _update_job(job_id, stage=f"Thumbnail upload failed: {str(exc)[:80]}", progress=93)

        # ── Set schedule ───────────────────────────────────────────────────
        if job.scheduled_publish_at:
            _update_job(job_id, stage="Setting schedule…", progress=96)
            try:
                yt_core.update_video(
                    video_id=video_id,
                    privacy_status="private",
                    publish_at=job.scheduled_publish_at,
                )
                job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
                job.schedule_set = True
                db.commit()
                logger.info(
                    "Schedule set: video_id=%s publish_at=%s",
                    video_id, job.scheduled_publish_at,
                )
            except Exception as exc:
                logger.warning("Schedule set failed for %s: %s", video_id, exc)
                _update_job(job_id, stage=f"Schedule failed: {str(exc)[:80]}", progress=96)
        else:
            # No scheduling — just update to configured privacy
            try:
                yt_core.update_video(
                    video_id=video_id,
                    privacy_status=job.youtube_privacy_status,
                )
            except Exception as exc:
                logger.warning("Privacy update failed: %s", exc)

        _update_job(job_id, stage="YouTube upload complete.", progress=99)

    finally:
        db.close()


# ── Retry helper ──────────────────────────────────────────────────────────────

_TRANSIENT_KEYWORDS = (
    "timeout", "rate limit", "rate-limit", "ratelimit", "429", "503", "502",
    "connection", "network", "refused", "ssl", "ttl", "temporary",
    "duckduckgo rate", "too many requests",
)

# Exception types that are always transient regardless of message content
_TRANSIENT_EXCEPTION_TYPES: tuple = ()  # populated after import below


def _is_transient(exc: Exception) -> bool:
    """
    Return True if the exception represents a temporary/retriable error.
    Checks both the exception message and the exception type.
    """
    from backend.services.research.base import ResearchRateLimitError, ResearchNetworkError
    # Rate-limit and network errors are always transient by type
    if isinstance(exc, (ResearchRateLimitError, ResearchNetworkError)):
        return True
    msg = str(exc).lower()
    return any(k in msg for k in _TRANSIENT_KEYWORDS)


def _retry_call(
    fn,
    job_id: str,
    stage_name: str,
    max_retries: int = 3,
    permanent_exceptions: tuple = (),
) -> object:
    """
    Call fn() with bounded configurable-delay retry on transient errors.
    Permanent errors raise immediately without retry.
    Phase 3B: uses QUEUE_RETRY_DELAY_N env vars.

    Research-specific behaviour:
      - ResearchRateLimitError → always transient (DDG rate limit, wait and retry)
      - ResearchNetworkError   → always transient
      - ResearchNoSourcesError → permanent (topic found nothing; retrying won't help)
    """
    from backend.services.queue_services import log_job, get_retry_delay
    from backend.services.research.base import ResearchNoSourcesError, ResearchEmptyTopicError

    # These are never retriable — fail immediately with a clear message
    _permanent_by_type = (ResearchNoSourcesError, ResearchEmptyTopicError) + permanent_exceptions

    last_exc = None
    for attempt in range(1, max_retries + 2):  # max_retries + 1 attempts total
        try:
            return fn()
        except _permanent_by_type as exc:
            # Permanent failure — log clearly and re-raise immediately
            log_job(
                job_id,
                f"[{stage_name}] Permanent failure (will not retry): {str(exc)[:200]}",
                level="error", stage=stage_name,
            )
            raise exc
        except Exception as exc:
            last_exc = exc
            if not _is_transient(exc):
                logger.warning(
                    "[%s] Non-transient error on attempt %d: %s",
                    stage_name, attempt, str(exc)[:120],
                )
                log_job(
                    job_id,
                    f"[{stage_name}] Non-transient error: {str(exc)[:200]}",
                    level="error", stage=stage_name,
                )
                raise exc
            if attempt > max_retries:
                break
            delay = get_retry_delay(attempt)
            # For research/DDG rate limits, enforce a minimum 30s delay
            from backend.services.research.base import ResearchRateLimitError
            if isinstance(exc, ResearchRateLimitError):
                delay = max(delay, 30)
            msg = (
                f"[{stage_name}] Transient error attempt {attempt}/{max_retries}: "
                f"{type(exc).__name__}: {str(exc)[:80]}. Retrying in {delay}s."
            )
            logger.warning(msg)
            log_job(job_id, msg, level="warning", stage=stage_name)
            time.sleep(delay)

    raise last_exc


# ── DB update helpers ─────────────────────────────────────────────────────────

def _update_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    progress: Optional[int] = None,
    completed_at: Optional[datetime] = None,
) -> None:
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob

    db = SessionLocal()
    try:
        job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
        if not job:
            return
        if status   is not None: job.status        = status
        if stage    is not None: job.current_stage = stage
        if progress is not None: job.progress      = progress
        if completed_at is not None: job.completed_at = completed_at
        db.commit()
    except Exception as exc:
        logger.warning("_update_job failed for %s: %s", job_id, exc)
    finally:
        db.close()


def _mark_failed(job_id: str, message: str) -> None:
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob, QueueStatus
    from backend.services.queue_services import log_job, get_retry_delay
    from datetime import timedelta

    db = SessionLocal()
    try:
        job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
        if not job:
            return
        if job.retry_count < job.max_retries:
            job.retry_count += 1
            next_attempt = job.retry_count
            delay_s = get_retry_delay(next_attempt)
            job.status        = QueueStatus.QUEUED
            job.current_stage = f"Retrying (attempt {job.retry_count}/{job.max_retries})…"
            job.progress      = 0
            job.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_s)
            db.commit()
            log_job(
                job_id,
                f"Retrying job (attempt {job.retry_count}/{job.max_retries}) in {delay_s}s: {message[:120]}",
                level="warning", stage="retry",
            )
            logger.info("Requeuing job %s for retry %d/%d", job_id, job.retry_count, job.max_retries)
        else:
            job.status        = QueueStatus.FAILED
            job.error_message = message[:500]
            job.current_stage = "Failed"
            job.failed_at     = datetime.now(timezone.utc)
            db.commit()
            log_job(job_id, f"Job permanently failed: {message[:200]}", level="error", stage="failed")
            logger.warning("Job %s permanently failed: %s", job_id, message[:120])
    except Exception as exc:
        logger.error("_mark_failed failed: %s", exc)
    finally:
        db.close()


# ── Topic sanitisation ────────────────────────────────────────────────────────

def _sanitise_topic(topic: str) -> str:
    """
    Clean a user-supplied topic before passing it to the research provider.

    Removes:
    - Surrounding quote characters (single/double/smart quotes) that prevent
      DDG from finding results. E.g. '"Why cats purr"' → 'Why cats purr'
    - Leading/trailing whitespace
    - Excessive punctuation that confuses search queries

    Does NOT truncate — ScriptRequest already validates max length.
    """
    import re as _re
    t = topic.strip()
    # Remove surrounding matching quotes: "...", '...', "...", '...'
    for open_q, close_q in [('"', '"'), ("'", "'"), ('\u201c', '\u201d'), ('\u2018', '\u2019')]:
        if t.startswith(open_q) and t.endswith(close_q) and len(t) > 2:
            t = t[1:-1].strip()
    # Remove any remaining lone leading/trailing quote chars
    t = t.strip('"\'')
    # Collapse multiple spaces
    t = _re.sub(r'\s+', ' ', t).strip()
    if t != topic.strip():
        logger.info("Topic sanitised: %r → %r", topic.strip(), t)
    return t or topic.strip()  # fall back to original if sanitisation empties it


# ── Startup recovery ──────────────────────────────────────────────────────────

def _recover_stale_jobs() -> None:
    """
    Called on worker startup. Finds jobs that were left in an active state
    (e.g. server crashed mid-pipeline) and requeues them for retry,
    or marks them failed if retries exhausted.

    Recovery strategy:
      - Jobs in ACTIVE states are requeued (not re-uploaded if youtube_video_id set).
      - Jobs with youtube_video_id set are considered completed and updated accordingly.
    """
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob, QueueStatus

    db = SessionLocal()
    try:
        stale = (
            db.query(ContentQueueJob)
            .filter(ContentQueueJob.status.in_(list(QueueStatus.ACTIVE)))
            .all()
        )
        for job in stale:
            if job.youtube_video_id:
                # Upload succeeded before crash — mark completed
                job.status        = QueueStatus.COMPLETED
                job.current_stage = "Recovered (video was uploaded before restart)"
                job.progress      = 100
                if not job.completed_at:
                    job.completed_at = datetime.now(timezone.utc)
                logger.info("Recovered job %s as completed (video_id=%s)", job.id, job.youtube_video_id)
            elif job.retry_count < job.max_retries:
                job.retry_count  += 1
                job.status        = QueueStatus.QUEUED
                job.current_stage = f"Requeued after restart (attempt {job.retry_count}/{job.max_retries})"
                job.progress      = 0
                logger.info("Requeued stale job %s for retry", job.id)
            else:
                job.status        = QueueStatus.FAILED
                job.error_message = "Job was in active state on startup with no retries remaining. Server may have crashed."
                job.current_stage = "Failed"
                logger.warning("Marked stale job %s as failed (no retries left)", job.id)
        if stale:
            db.commit()
            logger.info("Startup recovery: processed %d stale job(s)", len(stale))
    except Exception as exc:
        logger.error("Startup recovery failed: %s", exc)
    finally:
        db.close()
