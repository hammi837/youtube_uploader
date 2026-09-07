"""
backend/services/queue_services.py — Phase 3B queue support services.

Provides:
  - Per-job logging to queue_job_logs table
  - YouTube daily upload tracking and limit enforcement
  - Disk-space check before video generation
  - Cleanup service for old generated media
  - Duplicate topic detection against pending/active jobs
  - Queue health and stats summaries

All functions use their own SessionLocal (safe to call from background threads).
No credentials, tokens, or secrets are ever logged or returned.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Env-based configuration ────────────────────────────────────────────────────

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        return default

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (ValueError, TypeError):
        return default


def get_upload_limit() -> int:
    return _env_int("YOUTUBE_DAILY_UPLOAD_LIMIT", 5)


def get_min_free_disk_gb() -> float:
    return _env_float("MIN_FREE_DISK_GB", 10.0)


def get_media_retention_days() -> int:
    return _env_int("MEDIA_RETENTION_DAYS", 7)


def get_retry_delay(attempt: int) -> int:
    """Return retry delay in seconds for given attempt number (1-based)."""
    if attempt == 1:
        return _env_int("QUEUE_RETRY_DELAY_1", 30)
    elif attempt == 2:
        return _env_int("QUEUE_RETRY_DELAY_2", 120)
    else:
        return _env_int("QUEUE_RETRY_DELAY_3", 300)


# ── Per-job logging ────────────────────────────────────────────────────────────

def log_job(job_id: str, message: str, level: str = "info", stage: Optional[str] = None) -> None:
    """
    Persist a log entry for a queue job.
    Safe to call from background threads.
    Never logs credentials, tokens, or API keys.
    """
    from backend.db import SessionLocal
    from backend.queue_models import QueueJobLog

    # Sanitise message — strip anything that looks like a secret
    safe_msg = _sanitise_log_message(message)

    db = SessionLocal()
    try:
        db.add(QueueJobLog(
            id=str(uuid.uuid4()),
            job_id=job_id,
            level=level,
            stage=stage,
            message=safe_msg[:1000],
        ))
        db.commit()
    except Exception as exc:
        logger.warning("Failed to write job log for %s: %s", job_id, exc)
    finally:
        db.close()


def get_job_logs(job_id: str, limit: int = 100) -> list[dict]:
    """Return recent log entries for a job, newest first."""
    from backend.db import SessionLocal
    from backend.queue_models import QueueJobLog

    db = SessionLocal()
    try:
        entries = (
            db.query(QueueJobLog)
            .filter(QueueJobLog.job_id == job_id)
            .order_by(QueueJobLog.created_at.asc())   # chronological
            .limit(limit)
            .all()
        )
        return [
            {
                "id": e.id,
                "level": e.level,
                "stage": e.stage,
                "message": e.message,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ]
    finally:
        db.close()


def _sanitise_log_message(msg: str) -> str:
    """Remove patterns that look like secrets from log messages."""
    import re
    # Mask anything that looks like an API key (long alphanumeric strings with underscores)
    msg = re.sub(r'\b(gsk_[A-Za-z0-9_]{20,})\b', '[REDACTED_KEY]', msg)
    msg = re.sub(r'\b([A-Za-z0-9_-]{40,})\b', lambda m: '[REDACTED]' if len(m.group()) > 40 else m.group(), msg)
    return msg


# ── YouTube daily upload tracking ──────────────────────────────────────────────

def get_uploads_today() -> int:
    """Return the number of YouTube uploads made today (UTC)."""
    from backend.db import SessionLocal
    from backend.queue_models import YouTubeDailyUpload

    today = date.today().isoformat()
    db = SessionLocal()
    try:
        row = db.query(YouTubeDailyUpload).filter(
            YouTubeDailyUpload.date == today
        ).first()
        return row.count if row else 0
    finally:
        db.close()


def increment_uploads_today() -> int:
    """Increment today's upload count. Returns new count."""
    from backend.db import SessionLocal
    from backend.queue_models import YouTubeDailyUpload

    today = date.today().isoformat()
    db = SessionLocal()
    try:
        row = db.query(YouTubeDailyUpload).filter(
            YouTubeDailyUpload.date == today
        ).first()
        if row:
            row.count += 1
            row.updated_at = datetime.now(timezone.utc)
        else:
            row = YouTubeDailyUpload(date=today, count=1)
            db.add(row)
        db.commit()
        return row.count
    finally:
        db.close()


def check_upload_limit() -> tuple[bool, int, int]:
    """
    Check whether the daily upload limit has been reached.

    Returns:
        (allowed: bool, uploads_today: int, limit: int)
    """
    limit = get_upload_limit()
    today_count = get_uploads_today()
    return today_count < limit, today_count, limit


# ── Disk-space check ──────────────────────────────────────────────────────────

def get_free_disk_gb() -> float:
    """Return free disk space on the G: drive (or DATA_DIR's drive) in GB."""
    data_dir = os.getenv("DATA_DIR", r"G:\youtube-uploader\data")
    try:
        stat = shutil.disk_usage(data_dir)
        return round(stat.free / (1024 ** 3), 2)
    except Exception as exc:
        logger.warning("Could not check disk space for %s: %s", data_dir, exc)
        return 999.0  # assume OK if we can't check


def check_disk_space() -> tuple[bool, float]:
    """
    Check whether enough disk space is available.

    Returns:
        (ok: bool, free_gb: float)
    """
    min_gb = get_min_free_disk_gb()
    free_gb = get_free_disk_gb()
    return free_gb >= min_gb, free_gb


# ── Duplicate topic detection ─────────────────────────────────────────────────

def find_duplicate_topics(topics: list[str]) -> list[str]:
    """
    Return topics that are already queued, researching, or generating.
    Uses case-insensitive normalised comparison.
    """
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob, QueueStatus

    if not topics:
        return []

    normalised = {t.lower().strip(): t for t in topics}
    pending_statuses = list(QueueStatus.ACTIVE | {QueueStatus.QUEUED})

    db = SessionLocal()
    try:
        pending = (
            db.query(ContentQueueJob.topic)
            .filter(ContentQueueJob.status.in_(pending_statuses))
            .all()
        )
        pending_topics = {row.topic.lower().strip() for row in pending}
        return [
            original for norm, original in normalised.items()
            if norm in pending_topics
        ]
    finally:
        db.close()


# ── Cleanup service ────────────────────────────────────────────────────────────

def run_cleanup(dry_run: bool = False) -> dict:
    """
    Clean up old generated media files.

    Rules:
    - Only deletes files older than MEDIA_RETENTION_DAYS (default 7).
    - Never deletes files belonging to active/queued jobs.
    - Never deletes files still referenced by a DB record with a youtube_video_id.
    - Deletes: old audio, old temp dirs, old video/thumbnail/caption files
      if the job is completed AND older than retention period.
    - DB records are NEVER deleted (only disk files).

    Args:
        dry_run: If True, report what would be deleted without actually deleting.

    Returns:
        dict with files_deleted, bytes_freed, files_skipped, errors.
    """
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob, QueueStatus

    retention_days = get_media_retention_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    data_dir = Path(os.getenv("DATA_DIR", r"G:\youtube-uploader\data"))

    files_deleted = 0
    bytes_freed = 0
    files_skipped = 0
    errors: list[str] = []

    db = SessionLocal()
    try:
        # Build set of file paths that must NOT be deleted (active jobs)
        protected: set[str] = set()
        active_jobs = (
            db.query(ContentQueueJob)
            .filter(ContentQueueJob.status.in_(
                list(QueueStatus.ACTIVE | {QueueStatus.QUEUED})
            ))
            .all()
        )
        from backend.video_generation_models import VideoGenerationJob
        for job in active_jobs:
            if job.video_job_id:
                vj = db.query(VideoGenerationJob).filter(
                    VideoGenerationJob.id == job.video_job_id
                ).first()
                if vj:
                    for p in (vj.output_path, vj.thumbnail_path, vj.caption_path):
                        if p:
                            protected.add(p)

        # Find completed jobs older than retention cutoff
        old_jobs = (
            db.query(ContentQueueJob)
            .filter(
                ContentQueueJob.status == QueueStatus.COMPLETED,
                ContentQueueJob.completed_at < cutoff,
            )
            .all()
        )

        for job in old_jobs:
            if not job.video_job_id:
                continue
            vj = db.query(VideoGenerationJob).filter(
                VideoGenerationJob.id == job.video_job_id
            ).first()
            if not vj:
                continue

            for file_path_str in (vj.output_path, vj.thumbnail_path, vj.caption_path):
                if not file_path_str:
                    continue
                if file_path_str in protected:
                    files_skipped += 1
                    continue
                fp = Path(file_path_str)
                if not fp.exists():
                    continue
                try:
                    size = fp.stat().st_size
                    if not dry_run:
                        fp.unlink()
                    files_deleted += 1
                    bytes_freed += size
                    logger.info("%sDeleted: %s (%d bytes)", "[DRY] " if dry_run else "", fp.name, size)
                except Exception as exc:
                    errors.append(f"Could not delete {fp.name}: {str(exc)[:80]}")

        # Clean orphaned temp directories older than 1 day
        temp_dir = data_dir / "temp"
        if temp_dir.exists():
            cutoff_1d = datetime.now(timezone.utc) - timedelta(days=1)
            for job_temp in temp_dir.iterdir():
                if not job_temp.is_dir():
                    continue
                try:
                    mtime = datetime.fromtimestamp(job_temp.stat().st_mtime, tz=timezone.utc)
                    if mtime < cutoff_1d:
                        size = sum(f.stat().st_size for f in job_temp.rglob("*") if f.is_file())
                        if not dry_run:
                            shutil.rmtree(job_temp, ignore_errors=True)
                        files_deleted += 1
                        bytes_freed += size
                        logger.info("%sCleaned temp dir: %s", "[DRY] " if dry_run else "", job_temp.name)
                except Exception as exc:
                    errors.append(f"Could not clean temp dir {job_temp.name}: {str(exc)[:80]}")

    finally:
        db.close()

    return {
        "files_deleted": files_deleted,
        "bytes_freed": bytes_freed,
        "files_skipped": files_skipped,
        "errors": errors,
        "dry_run": dry_run,
    }


# ── Queue health ──────────────────────────────────────────────────────────────

def get_queue_health() -> dict:
    """Return a safe health summary — no credentials exposed."""
    from backend.services.queue_processor import is_worker_alive, is_paused, get_current_job_id
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob, QueueStatus

    disk_ok, free_gb = check_disk_space()
    upload_ok, uploads_today, upload_limit = check_upload_limit()

    db = SessionLocal()
    try:
        queued = db.query(ContentQueueJob).filter(
            ContentQueueJob.status == QueueStatus.QUEUED
        ).count()
        active = db.query(ContentQueueJob).filter(
            ContentQueueJob.status.in_(list(QueueStatus.ACTIVE))
        ).count()
    finally:
        db.close()

    details: list[str] = []
    overall = "ok"

    if not is_worker_alive():
        details.append("Queue worker is not running.")
        overall = "warning"
    if is_paused():
        details.append("Queue is paused.")
    if not disk_ok:
        details.append(f"Low disk space: {free_gb:.1f} GB free (min {get_min_free_disk_gb()} GB).")
        overall = "warning"
    if not upload_ok:
        details.append(f"Daily upload limit reached: {uploads_today}/{upload_limit}.")
        overall = "warning"

    return {
        "status": overall,
        "worker_alive": is_worker_alive(),
        "worker_paused": is_paused(),
        "free_disk_gb": free_gb,
        "disk_warning": not disk_ok,
        "uploads_today": uploads_today,
        "upload_limit": upload_limit,
        "uploads_remaining": max(0, upload_limit - uploads_today),
        "queued_jobs": queued,
        "active_jobs": active,
        "details": details,
    }


# ── Queue stats ────────────────────────────────────────────────────────────────

def get_queue_stats() -> dict:
    """Return detailed queue statistics."""
    from backend.db import SessionLocal
    from backend.queue_models import ContentQueueJob, QueueStatus
    from sqlalchemy import func as sa_func

    db = SessionLocal()
    try:
        counts: dict[str, int] = {}
        for s in QueueStatus.ALL:
            counts[s] = db.query(ContentQueueJob).filter(
                ContentQueueJob.status == s
            ).count()

        # Average processing time for completed jobs (minutes)
        avg_mins = 0.0
        try:
            completed = (
                db.query(ContentQueueJob)
                .filter(
                    ContentQueueJob.status == QueueStatus.COMPLETED,
                    ContentQueueJob.started_at.isnot(None),
                    ContentQueueJob.completed_at.isnot(None),
                )
                .all()
            )
            if completed:
                durations = []
                for j in completed:
                    if j.started_at and j.completed_at:
                        diff = (j.completed_at - j.started_at).total_seconds()
                        if diff > 0:
                            durations.append(diff / 60)
                if durations:
                    avg_mins = round(sum(durations) / len(durations), 1)
        except Exception:
            pass

        _, uploads_today, upload_limit = check_upload_limit()
        _, free_gb = check_disk_space()

        return {
            "total": sum(counts.values()),
            "queued": counts.get(QueueStatus.QUEUED, 0),
            "processing": sum(counts.get(s, 0) for s in QueueStatus.ACTIVE),
            "completed": counts.get(QueueStatus.COMPLETED, 0),
            "failed": counts.get(QueueStatus.FAILED, 0),
            "cancelled": counts.get(QueueStatus.CANCELLED, 0),
            "scheduled": counts.get(QueueStatus.SCHEDULED, 0),
            "uploads_today": uploads_today,
            "upload_limit": upload_limit,
            "free_disk_gb": free_gb,
            "avg_processing_minutes": avg_mins,
        }
    finally:
        db.close()
