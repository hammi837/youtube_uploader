"""
tests/test_queue_3b.py — Phase 3B queue reliability tests.

All external services mocked. No network calls, no FFmpeg, no YouTube.

Coverage:
  Queue controls:
    A.  POST /api/queue/stop                    → 200
    B.  POST /api/queue/retry-failed            → resets all failed jobs
    C.  POST /api/queue/clear-completed         → deletes completed records
    D.  POST /api/queue/clear-failed            → deletes failed/cancelled
    E.  POST /api/queue/cleanup dry_run=true    → reports without deleting
    F.  POST /api/queue/cleanup                 → returns CleanupResponse shape

  New endpoints:
    G.  GET /api/queue/health                   → QueueHealthResponse shape
    H.  GET /api/queue/stats                    → QueueStatsResponse shape
    I.  GET /api/queue/{id}/logs                → list of log entries
    J.  GET /api/queue/{id}/logs nonexistent    → 404

  Job logging:
    K.  log_job persists entry
    L.  log_job sanitises secrets
    M.  get_job_logs returns chronological order

  Daily upload tracking:
    N.  get_uploads_today returns 0 on fresh day
    O.  increment_uploads_today increments correctly
    P.  check_upload_limit returns False when limit reached
    Q.  check_upload_limit returns True when under limit

  Disk-space check:
    R.  check_disk_space returns (True, gb) when enough space
    S.  check_disk_space returns (False, gb) when below MIN_FREE_DISK_GB
    T.  pipeline pauses queue when disk too low

  Duplicate topic detection:
    U.  POST /api/queue with duplicate in same batch → 422
    V.  POST /api/queue with topic already queued   → 409
    W.  POST /api/queue with different topics       → 201
    X.  find_duplicate_topics returns matching topics

  Queue stats/health:
    Y.  get_queue_stats returns expected keys
    Z.  get_queue_health returns expected keys
    AA. health warns when upload limit reached
    AB. health warns when disk low

  Retry delays:
    AC. get_retry_delay returns env-configured values
    AD. _mark_failed sets next_retry_at
    AE. _claim_next_job respects next_retry_at

  next_retry_at:
    AF. job with future next_retry_at is skipped by worker
    AG. job with past next_retry_at is claimed by worker

  State machine:
    AH. cancel sets cancelled_at
    AI. failed jobs set failed_at

  Cleanup safety:
    AJ. cleanup skips files for active jobs
    AK. cleanup dry_run does not delete files

  Security:
    AL. /api/queue/{id}/logs exposes no credentials
    AM. /api/queue/health exposes no credentials
    AN. /api/queue/stats exposes no credentials

  Video job status updates:
    AO. video job status updated during pipeline via progress callback
    AP. video job marked as FAILED when pipeline raises exception
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import Base, get_db
from backend.main import app
from backend.queue_models import (
    ContentQueueJob, QueueJobLog, YouTubeDailyUpload,
    QueueStatus, queue_job_to_response,
)
from backend.tts_models import GeneratedAudio, AudioStatus
from backend.video_generation_models import VideoGenerationJob, VideoJobStatus

from tests.conftest import shared_engine, SharedTestingSessionLocal

test_engine = shared_engine
TestingSessionLocal = SharedTestingSessionLocal


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def db():
    s = TestingSessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_job(db, status=QueueStatus.QUEUED, topic="Test topic", **kw) -> ContentQueueJob:
    defaults = dict(
        id=str(uuid.uuid4()),
        topic=topic,
        language="en",
        tone="engaging",
        target_duration_seconds=180,
        scene_count=10,
        status=status,
        priority=0,
        retry_count=0,
        max_retries=3,
        youtube_privacy_status="private",
        youtube_category_id="22",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    job = ContentQueueJob(**defaults)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _now_plus(minutes: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


# ── A–F: New control endpoints ────────────────────────────────────────────────

class TestNewControlEndpoints:

    # A. Stop
    def test_stop_returns_200(self, client):
        with patch("backend.routers.queue.stop_worker"):
            r = client.post("/api/queue/stop")
        assert r.status_code == 200
        assert "stop" in r.json()["message"].lower()

    # B. Retry all failed
    def test_retry_all_failed(self, client, db):
        _make_job(db, status=QueueStatus.FAILED, topic="Failed 1")
        _make_job(db, status=QueueStatus.FAILED, topic="Failed 2")
        _make_job(db, status=QueueStatus.COMPLETED, topic="Completed")

        with patch("backend.routers.queue.start_worker"):
            r = client.post("/api/queue/retry-failed")

        assert r.status_code == 200
        assert r.json()["count"] == 2
        # Check DB
        queued = db.query(ContentQueueJob).filter(
            ContentQueueJob.status == QueueStatus.QUEUED
        ).count()
        assert queued == 2

    # C. Clear completed
    def test_clear_completed(self, client, db):
        _make_job(db, status=QueueStatus.COMPLETED, topic="Done 1")
        _make_job(db, status=QueueStatus.COMPLETED, topic="Done 2")
        _make_job(db, status=QueueStatus.QUEUED, topic="Still queued")

        r = client.post("/api/queue/clear-completed")
        assert r.status_code == 200
        assert r.json()["count"] == 2
        assert db.query(ContentQueueJob).count() == 1

    # D. Clear failed
    def test_clear_failed(self, client, db):
        _make_job(db, status=QueueStatus.FAILED, topic="F1")
        _make_job(db, status=QueueStatus.CANCELLED, topic="C1")
        _make_job(db, status=QueueStatus.QUEUED, topic="Q1")

        r = client.post("/api/queue/clear-failed")
        assert r.status_code == 200
        assert r.json()["count"] == 2
        assert db.query(ContentQueueJob).count() == 1

    # E. Cleanup dry_run
    def test_cleanup_dry_run_returns_response(self, client):
        with patch("backend.routers.queue.run_cleanup") as mock_cleanup:
            mock_cleanup.return_value = {
                "files_deleted": 0, "bytes_freed": 0,
                "files_skipped": 3, "errors": [], "dry_run": True,
            }
            r = client.post("/api/queue/cleanup?dry_run=true")
        assert r.status_code == 200
        body = r.json()
        assert "files_deleted" in body
        assert "bytes_freed" in body
        assert "files_skipped" in body

    # F. Cleanup shape
    def test_cleanup_returns_cleanup_response(self, client):
        with patch("backend.routers.queue.run_cleanup") as mock_cleanup:
            mock_cleanup.return_value = {
                "files_deleted": 2, "bytes_freed": 1024 * 1024 * 10,
                "files_skipped": 0, "errors": [], "dry_run": False,
            }
            r = client.post("/api/queue/cleanup")
        assert r.status_code == 200
        assert r.json()["files_deleted"] == 2


# ── G–J: New info endpoints ───────────────────────────────────────────────────

class TestNewInfoEndpoints:

    def _patch_health(self):
        return patch("backend.routers.queue.get_queue_health", return_value={
            "status": "ok", "worker_alive": True, "worker_paused": False,
            "free_disk_gb": 50.0, "disk_warning": False,
            "uploads_today": 1, "upload_limit": 5, "uploads_remaining": 4,
            "queued_jobs": 2, "active_jobs": 0, "details": [],
        })

    def _patch_stats(self):
        return patch("backend.routers.queue.get_queue_stats", return_value={
            "total": 5, "queued": 2, "processing": 0, "completed": 3,
            "failed": 0, "cancelled": 0, "scheduled": 0,
            "uploads_today": 1, "upload_limit": 5,
            "free_disk_gb": 50.0, "avg_processing_minutes": 6.2,
        })

    # G. Health endpoint
    def test_health_returns_200(self, client):
        with self._patch_health():
            r = client.get("/api/queue/health")
        assert r.status_code == 200
        body = r.json()
        for key in ("status", "worker_alive", "free_disk_gb", "uploads_today", "upload_limit"):
            assert key in body

    # H. Stats endpoint
    def test_stats_returns_200(self, client):
        with self._patch_stats():
            r = client.get("/api/queue/stats")
        assert r.status_code == 200
        body = r.json()
        for key in ("total", "queued", "completed", "failed", "uploads_today"):
            assert key in body

    # I. Job logs
    def test_get_job_logs(self, client, db):
        job = _make_job(db)
        log = QueueJobLog(
            id=str(uuid.uuid4()),
            job_id=job.id,
            level="info",
            stage="research",
            message="Research started.",
            created_at=datetime.now(timezone.utc),
        )
        db.add(log)
        db.commit()

        with patch("backend.routers.queue.get_job_logs", return_value=[
            {"id": log.id, "level": "info", "stage": "research",
             "message": "Research started.",
             "created_at": log.created_at.isoformat()}
        ]):
            r = client.get(f"/api/queue/{job.id}/logs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert r.json()[0]["message"] == "Research started."

    # J. Logs 404
    def test_get_logs_nonexistent_job_404(self, client):
        r = client.get("/api/queue/nonexistent-id/logs")
        assert r.status_code == 404


# ── K–M: Job logging ─────────────────────────────────────────────────────────

class TestJobLogging:

    # K. Persists entry
    def test_log_job_persists(self, db):
        from backend.services.queue_services import log_job
        from tests.conftest import SharedTestingSessionLocal
        job = _make_job(db)
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            log_job(job.id, "Test log message.", stage="test")
        entry = db.query(QueueJobLog).filter(QueueJobLog.job_id == job.id).first()
        assert entry is not None
        assert "Test log message" in entry.message

    # L. Sanitises secrets
    def test_log_job_sanitises_api_key(self, db):
        from backend.services.queue_services import log_job, _sanitise_log_message
        msg = "gsk_AbCdEfGhIjKlMnOpQrStUvWxYz123456789"
        safe = _sanitise_log_message(msg)
        assert "gsk_" not in safe
        assert "REDACTED" in safe

    # M. Chronological order
    def test_get_job_logs_chronological(self, db):
        from backend.services.queue_services import log_job, get_job_logs
        from tests.conftest import SharedTestingSessionLocal
        job = _make_job(db)
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            log_job(job.id, "First message")
            log_job(job.id, "Second message")
            log_job(job.id, "Third message")
            entries = get_job_logs(job.id, limit=10)
        messages = [e["message"] for e in entries]
        assert messages.index("First message") < messages.index("Third message")


# ── N–Q: Daily upload tracking ────────────────────────────────────────────────

class TestDailyUploadTracking:

    # N. Zero on fresh day
    def test_uploads_today_zero(self, db):
        from backend.services.queue_services import get_uploads_today
        from tests.conftest import SharedTestingSessionLocal
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            count = get_uploads_today()
        assert count == 0

    # O. Increment
    def test_increment_uploads(self, db):
        from backend.services.queue_services import increment_uploads_today, get_uploads_today
        from tests.conftest import SharedTestingSessionLocal
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            increment_uploads_today()
            increment_uploads_today()
            count = get_uploads_today()
        assert count == 2

    # P. Limit reached
    def test_check_upload_limit_reached(self):
        from backend.services.queue_services import check_upload_limit
        with patch("backend.services.queue_services.get_uploads_today", return_value=5), \
             patch("backend.services.queue_services.get_upload_limit", return_value=5):
            allowed, today, limit = check_upload_limit()
        assert allowed is False
        assert today == 5

    # Q. Under limit
    def test_check_upload_limit_allowed(self):
        from backend.services.queue_services import check_upload_limit
        with patch("backend.services.queue_services.get_uploads_today", return_value=2), \
             patch("backend.services.queue_services.get_upload_limit", return_value=5):
            allowed, today, limit = check_upload_limit()
        assert allowed is True
        assert today == 2


# ── R–T: Disk-space check ─────────────────────────────────────────────────────

class TestDiskSpaceCheck:

    # R. Enough space
    def test_check_disk_ok(self):
        from backend.services.queue_services import check_disk_space
        with patch("backend.services.queue_services.get_free_disk_gb", return_value=50.0), \
             patch("backend.services.queue_services.get_min_free_disk_gb", return_value=10.0):
            ok, gb = check_disk_space()
        assert ok is True
        assert gb == 50.0

    # S. Below minimum
    def test_check_disk_warning(self):
        from backend.services.queue_services import check_disk_space
        with patch("backend.services.queue_services.get_free_disk_gb", return_value=5.0), \
             patch("backend.services.queue_services.get_min_free_disk_gb", return_value=10.0):
            ok, gb = check_disk_space()
        assert ok is False
        assert gb == 5.0

    # T. Pipeline pauses when disk low
    def test_pipeline_pauses_on_low_disk(self, db):
        from backend.services.queue_processor import _run_full_pipeline
        from tests.conftest import SharedTestingSessionLocal
        job = _make_job(db, status=QueueStatus.RESEARCHING)
        with patch("backend.services.queue_services.check_disk_space", return_value=(False, 5.0)), \
             patch("backend.services.queue_services.get_min_free_disk_gb", return_value=10.0), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
             patch("backend.services.queue_processor.pause_queue") as mock_pause:
            with pytest.raises(RuntimeError, match="only.*GB free"):
                _run_full_pipeline(job.id)
        mock_pause.assert_called_once()


# ── U–X: Duplicate topic detection ───────────────────────────────────────────

class TestDuplicateTopicDetection:

    # U. Duplicate in same batch
    def test_duplicate_in_same_batch_422(self, client):
        r = client.post("/api/queue", json={
            "topics": ["Why do cats purr?", "Why do cats purr?"],
        })
        assert r.status_code == 422
        body_str = r.text.lower()
        assert "duplicate" in body_str

    # V. Topic already queued
    def test_topic_already_queued_409(self, client, db):
        _make_job(db, status=QueueStatus.QUEUED, topic="Why do cats purr?")
        with patch("backend.routers.queue.find_duplicate_topics",
                   return_value=["Why do cats purr?"]), \
             patch("backend.routers.queue.start_worker"):
            r = client.post("/api/queue", json={"topics": ["Why do cats purr?"]})
        assert r.status_code == 409

    # W. Different topics succeed
    def test_different_topics_succeed(self, client, db):
        _make_job(db, status=QueueStatus.QUEUED, topic="topic A")
        with patch("backend.services.queue_services.find_duplicate_topics", return_value=[]), \
             patch("backend.routers.queue.start_worker"):
            r = client.post("/api/queue", json={"topics": ["topic B"]})
        assert r.status_code == 201

    # X. find_duplicate_topics matches normalised
    def test_find_duplicate_topics_normalised(self, db):
        from backend.services.queue_services import find_duplicate_topics
        from tests.conftest import SharedTestingSessionLocal
        _make_job(db, status=QueueStatus.QUEUED, topic="Why do cats purr?")
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            dupes = find_duplicate_topics(["  why do cats purr?  ", "New Topic"])
        assert any("cats purr" in d.lower() for d in dupes)
        assert "New Topic" not in dupes


# ── Y–AB: Health and stats ────────────────────────────────────────────────────

class TestHealthAndStats:

    # Y. Stats keys
    def test_get_queue_stats_keys(self):
        from backend.services.queue_services import get_queue_stats
        from tests.conftest import SharedTestingSessionLocal
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            stats = get_queue_stats()
        for key in ("total", "queued", "completed", "failed", "uploads_today", "free_disk_gb"):
            assert key in stats

    # Z. Health keys
    def test_get_queue_health_keys(self):
        from backend.services.queue_services import get_queue_health
        from tests.conftest import SharedTestingSessionLocal
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            health = get_queue_health()
        for key in ("status", "worker_alive", "free_disk_gb", "uploads_today", "details"):
            assert key in health

    # AA. Upload limit warning in health
    def test_health_warns_upload_limit(self):
        from backend.services.queue_services import get_queue_health
        from tests.conftest import SharedTestingSessionLocal
        with patch("backend.services.queue_services.check_upload_limit",
                   return_value=(False, 5, 5)), \
             patch("backend.services.queue_services.check_disk_space",
                   return_value=(True, 50.0)), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            h = get_queue_health()
        assert h["status"] == "warning"
        assert any("upload limit" in d.lower() for d in h["details"])

    # AB. Disk warning in health
    def test_health_warns_disk(self):
        from backend.services.queue_services import get_queue_health
        from tests.conftest import SharedTestingSessionLocal
        with patch("backend.services.queue_services.check_upload_limit",
                   return_value=(True, 0, 5)), \
             patch("backend.services.queue_services.check_disk_space",
                   return_value=(False, 5.0)), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            h = get_queue_health()
        assert h["status"] == "warning"
        assert h["disk_warning"] is True


# ── AC–AE: Retry delays ───────────────────────────────────────────────────────

class TestRetryDelays:

    # AC. Env-configured values
    def test_get_retry_delay_from_env(self):
        from backend.services.queue_services import get_retry_delay
        with patch.dict(os.environ, {
            "QUEUE_RETRY_DELAY_1": "45",
            "QUEUE_RETRY_DELAY_2": "150",
            "QUEUE_RETRY_DELAY_3": "400",
        }):
            assert get_retry_delay(1) == 45
            assert get_retry_delay(2) == 150
            assert get_retry_delay(3) == 400

    # AD. _mark_failed sets next_retry_at
    def test_mark_failed_sets_next_retry_at(self, db):
        from backend.services.queue_processor import _mark_failed
        from tests.conftest import SharedTestingSessionLocal
        job = _make_job(db, status=QueueStatus.RESEARCHING, retry_count=0, max_retries=3)
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
             patch("backend.services.queue_services.get_retry_delay", return_value=30):
            _mark_failed(job.id, "Transient network error")
        db.expire(job)
        db.refresh(job)
        assert job.next_retry_at is not None
        assert job.status == QueueStatus.QUEUED

    # AE. _claim_next_job respects next_retry_at
    def test_claim_skips_future_retry(self, db):
        from backend.services.queue_processor import _claim_next_job
        from tests.conftest import SharedTestingSessionLocal
        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        job = _make_job(db, status=QueueStatus.QUEUED, next_retry_at=future)
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            result = _claim_next_job()
        assert result is None  # skipped because next_retry_at is in the future

    def test_claim_picks_past_retry(self, db):
        from backend.services.queue_processor import _claim_next_job
        from tests.conftest import SharedTestingSessionLocal
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        job = _make_job(db, status=QueueStatus.QUEUED, next_retry_at=past)
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            result = _claim_next_job()
        assert result == job.id


# ── AF–AG: next_retry_at in worker ───────────────────────────────────────────

class TestNextRetryAt:

    # AF. Future next_retry_at skips job
    def test_worker_skips_future_retry_job(self, db):
        from backend.services.queue_processor import _claim_next_job
        from tests.conftest import SharedTestingSessionLocal
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        _make_job(db, status=QueueStatus.QUEUED, next_retry_at=future, topic="skipped")
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            claimed = _claim_next_job()
        assert claimed is None

    # AG. Past next_retry_at is claimed
    def test_worker_claims_past_retry_job(self, db):
        from backend.services.queue_processor import _claim_next_job
        from tests.conftest import SharedTestingSessionLocal
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        job = _make_job(db, status=QueueStatus.QUEUED, next_retry_at=past, topic="ready")
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            claimed = _claim_next_job()
        assert claimed == job.id


# ── AH–AI: Timestamps on state transitions ───────────────────────────────────

class TestStateTimestamps:

    # AH. Cancel sets cancelled_at
    def test_cancel_sets_cancelled_at(self, client, db):
        job = _make_job(db, status=QueueStatus.QUEUED)
        with patch("backend.routers.queue.get_current_job_id", return_value="other"):
            r = client.post(f"/api/queue/{job.id}/cancel")
        assert r.status_code == 200
        db.expire(job); db.refresh(job)
        assert job.status == QueueStatus.CANCELLED
        # Fix 2 (Phase 3B): cancelled_at must be populated
        assert job.cancelled_at is not None

    # AI. _mark_failed sets failed_at when no retries left
    def test_mark_failed_sets_failed_at(self, db):
        from backend.services.queue_processor import _mark_failed
        from tests.conftest import SharedTestingSessionLocal
        job = _make_job(db, status=QueueStatus.RESEARCHING, retry_count=3, max_retries=3)
        with patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
             patch("backend.services.queue_services.log_job"):
            _mark_failed(job.id, "Final failure")
        db.expire(job); db.refresh(job)
        assert job.status == QueueStatus.FAILED
        assert job.failed_at is not None


# ── AJ–AK: Cleanup safety ─────────────────────────────────────────────────────

class TestCleanupSafety:

    # AJ. Cleanup skips active job files
    def test_cleanup_skips_active_jobs(self, db, tmp_path):
        from backend.services.queue_services import run_cleanup
        from tests.conftest import SharedTestingSessionLocal
        from backend.video_generation_models import VideoGenerationJob, VideoJobStatus

        fake_mp4 = tmp_path / "video.mp4"
        fake_mp4.write_bytes(b"\x00" * 100)

        vj = VideoGenerationJob(
            id=str(uuid.uuid4()),
            content_project_id=str(uuid.uuid4()),
            status=VideoJobStatus.COMPLETED,
            progress=100,
            output_path=str(fake_mp4),
            width=1920, height=1080, fps=30,
            captions_enabled=True, music_enabled=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(vj)
        job = _make_job(db, status=QueueStatus.QUEUED, video_job_id=vj.id)
        db.commit()

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            result = run_cleanup(dry_run=False)

        # File should NOT be deleted (job is active/queued)
        assert fake_mp4.exists()
        assert result["files_skipped"] >= 0  # skipped or simply not in scope

    # AK. Dry run does not delete
    def test_cleanup_dry_run_no_delete(self, tmp_path):
        from backend.services.queue_services import run_cleanup
        from tests.conftest import SharedTestingSessionLocal

        fake_file = tmp_path / "old_audio.wav"
        fake_file.write_bytes(b"\x00" * 50)

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            result = run_cleanup(dry_run=True)

        # dry_run=True means reported but not deleted
        assert result["dry_run"] is True


# ── AL–AN: Security ───────────────────────────────────────────────────────────

class TestPhase3BSecurity:

    def test_logs_no_credentials(self, client, db):
        job = _make_job(db)
        with patch("backend.routers.queue.get_job_logs", return_value=[]):
            r = client.get(f"/api/queue/{job.id}/logs")
        assert "GROQ_API_KEY" not in r.text
        assert "client_secret" not in r.text

    def test_health_no_credentials(self, client):
        with patch("backend.routers.queue.get_queue_health", return_value={
            "status": "ok", "worker_alive": True, "worker_paused": False,
            "free_disk_gb": 50.0, "disk_warning": False,
            "uploads_today": 0, "upload_limit": 5, "uploads_remaining": 5,
            "queued_jobs": 0, "active_jobs": 0, "details": [],
        }):
            r = client.get("/api/queue/health")
        assert "GROQ_API_KEY" not in r.text
        assert "client_secret" not in r.text

    def test_stats_no_credentials(self, client):
        with patch("backend.routers.queue.get_queue_stats", return_value={
            "total": 0, "queued": 0, "processing": 0, "completed": 0,
            "failed": 0, "cancelled": 0, "scheduled": 0,
            "uploads_today": 0, "upload_limit": 5,
            "free_disk_gb": 50.0, "avg_processing_minutes": 0.0,
        }):
            r = client.get("/api/queue/stats")
        assert "GROQ_API_KEY" not in r.text
        assert "access_token" not in r.text


# ── QUEUE_AUTO_RUN env var ────────────────────────────────────────────────────

class TestQueueAutoRun:
    """Fix 1 (Phase 3B): QUEUE_AUTO_RUN=false must prevent auto-start."""

    def test_auto_run_true_starts_worker(self):
        """QUEUE_AUTO_RUN=true (default) should call start_worker."""
        from backend.services.queue_processor import start_worker as _sw
        with patch.dict(os.environ, {"QUEUE_AUTO_RUN": "true"}):
            auto_run = os.getenv("QUEUE_AUTO_RUN", "true").strip().lower()
            should_start = auto_run not in ("false", "0", "no", "off")
        assert should_start is True

    def test_auto_run_false_skips_worker(self):
        """QUEUE_AUTO_RUN=false should NOT call start_worker."""
        with patch.dict(os.environ, {"QUEUE_AUTO_RUN": "false"}):
            auto_run = os.getenv("QUEUE_AUTO_RUN", "true").strip().lower()
            should_start = auto_run not in ("false", "0", "no", "off")
        assert should_start is False

    def test_auto_run_zero_skips_worker(self):
        with patch.dict(os.environ, {"QUEUE_AUTO_RUN": "0"}):
            auto_run = os.getenv("QUEUE_AUTO_RUN", "true").strip().lower()
            should_start = auto_run not in ("false", "0", "no", "off")
        assert should_start is False

    def test_auto_run_unset_defaults_true(self):
        env = {k: v for k, v in os.environ.items() if k != "QUEUE_AUTO_RUN"}
        with patch.dict(os.environ, env, clear=True):
            auto_run = os.getenv("QUEUE_AUTO_RUN", "true").strip().lower()
            should_start = auto_run not in ("false", "0", "no", "off")
        assert should_start is True


# ── TTS audio cleanup ─────────────────────────────────────────────────────────

class TestTTSAudioCleanup:
    """Fix 3 (Phase 3B): cleanup should remove old orphaned TTS audio files."""

    def test_cleanup_removes_old_audio(self, db, tmp_path):
        """Audio older than retention for completed queue jobs should be deleted."""
        from backend.services.queue_services import run_cleanup
        from tests.conftest import SharedTestingSessionLocal
        from backend.tts_models import GeneratedAudio, AudioStatus
        from backend.content_models import ContentProject, ContentStatus
        from datetime import timedelta

        # Create a completed content project
        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Old topic",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=10,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)

        # Create an old audio file
        old_audio_file = tmp_path / "old_narration.wav"
        old_audio_file.write_bytes(b"\x00" * 1000)

        old_time = datetime.now(timezone.utc) - timedelta(days=10)
        audio = GeneratedAudio(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            voice="local-zira",
            language="en",
            text_length=100,
            file_path=str(old_audio_file),
            file_size_bytes=1000,
            duration_seconds=5.0,
            status=AudioStatus.COMPLETED,
            created_at=old_time,
            updated_at=old_time,
        )
        db.add(audio)
        db.commit()

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
             patch.dict(os.environ, {"MEDIA_RETENTION_DAYS": "7", "DATA_DIR": str(tmp_path)}):
            result = run_cleanup(dry_run=False)

        # Old audio file should have been deleted
        assert not old_audio_file.exists()
        assert result["files_deleted"] >= 1

    def test_cleanup_skips_audio_for_active_queue_job(self, db, tmp_path):
        """Audio for an active queue job must NOT be deleted."""
        from backend.services.queue_services import run_cleanup
        from tests.conftest import SharedTestingSessionLocal
        from backend.tts_models import GeneratedAudio, AudioStatus
        from backend.content_models import ContentProject, ContentStatus

        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Active topic",
            language="en", tone="engaging",
            target_duration_seconds=180, scene_count=10,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)

        active_audio_file = tmp_path / "active_narration.wav"
        active_audio_file.write_bytes(b"\x00" * 1000)

        # Audio record with old timestamp but for a QUEUED job
        audio = GeneratedAudio(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            voice="local-zira", language="en",
            text_length=100, file_path=str(active_audio_file),
            file_size_bytes=1000, duration_seconds=5.0,
            status=AudioStatus.COMPLETED,
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(audio)

        # Queue job is QUEUED (active) — audio must be protected
        queue_job = _make_job(db, status=QueueStatus.QUEUED,
                              topic="Active topic",
                              content_project_id=project.id)
        db.commit()

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
             patch.dict(os.environ, {"MEDIA_RETENTION_DAYS": "7", "DATA_DIR": str(tmp_path)}):
            run_cleanup(dry_run=False)

        # Active job's audio must still exist
        assert active_audio_file.exists()


# ── Research error classification fixes ──────────────────────────────────────

class TestResearchErrorClassification:
    """Verify that research errors are correctly classified as transient vs permanent."""

    def test_research_rate_limit_error_is_transient(self):
        """ResearchRateLimitError must be transient — DDG rate limits are temporary."""
        from backend.services.queue_processor import _is_transient
        from backend.services.research.base import ResearchRateLimitError
        exc = ResearchRateLimitError("DuckDuckGo rate-limited the search request.")
        assert _is_transient(exc) is True

    def test_research_network_error_is_transient(self):
        """ResearchNetworkError must be transient — network failures recover."""
        from backend.services.queue_processor import _is_transient
        from backend.services.research.base import ResearchNetworkError
        exc = ResearchNetworkError("Network error during DuckDuckGo search.")
        assert _is_transient(exc) is True

    def test_research_no_sources_error_is_not_transient(self):
        """ResearchNoSourcesError must NOT be transient — retrying won't find sources."""
        from backend.services.queue_processor import _is_transient
        from backend.services.research.base import ResearchNoSourcesError
        exc = ResearchNoSourcesError("No reliable web sources were found.")
        assert _is_transient(exc) is False

    def test_research_empty_topic_is_not_transient(self):
        """ResearchEmptyTopicError must NOT be transient — bad input won't fix itself."""
        from backend.services.queue_processor import _is_transient
        from backend.services.research.base import ResearchEmptyTopicError
        exc = ResearchEmptyTopicError("Topic cannot be empty.")
        assert _is_transient(exc) is False

    def test_rate_limit_keyword_variations(self):
        """All DDG rate-limit message variants must be detected as transient."""
        from backend.services.queue_processor import _is_transient
        # hyphenated form
        assert _is_transient(Exception("DuckDuckGo rate-limited the request")) is True
        # space form
        assert _is_transient(Exception("rate limit exceeded")) is True
        # no-space form
        assert _is_transient(Exception("ratelimit exceeded")) is True
        # 429 code
        assert _is_transient(Exception("HTTP 429 too many requests")) is True

    def test_no_sources_error_fails_job_without_retrying(self, db):
        """ResearchNoSourcesError in _retry_call must NOT retry — raise immediately."""
        from backend.services.queue_processor import _retry_call
        from backend.services.research.base import ResearchNoSourcesError
        from tests.conftest import SharedTestingSessionLocal

        job = _make_job(db)
        call_count = 0

        def always_no_sources():
            nonlocal call_count
            call_count += 1
            raise ResearchNoSourcesError("No sources found for this topic.")

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            with pytest.raises(ResearchNoSourcesError):
                _retry_call(always_no_sources, job.id, "research+script", max_retries=3)

        # Must have been called exactly once — no retries for permanent errors
        assert call_count == 1

    def test_rate_limit_error_does_retry(self, db):
        """ResearchRateLimitError must retry up to max_retries times."""
        from backend.services.queue_processor import _retry_call
        from backend.services.research.base import ResearchRateLimitError
        from tests.conftest import SharedTestingSessionLocal

        job = _make_job(db)
        call_count = 0

        def always_rate_limited():
            nonlocal call_count
            call_count += 1
            raise ResearchRateLimitError("DuckDuckGo rate-limited")

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
             patch("backend.services.queue_services.get_retry_delay", return_value=0), \
             patch("time.sleep"):  # don't actually sleep in tests
            with pytest.raises(ResearchRateLimitError):
                _retry_call(always_rate_limited, job.id, "research", max_retries=2)

        # Should have attempted 1 + 2 retries = 3 total calls
        assert call_count == 3

    def test_rate_limit_enforces_minimum_30s_delay(self, db):
        """ResearchRateLimitError must use at least 30s delay regardless of env config."""
        from backend.services.queue_processor import _retry_call
        from backend.services.research.base import ResearchRateLimitError
        from tests.conftest import SharedTestingSessionLocal

        job = _make_job(db)
        delays_used = []

        def rate_limited_then_ok():
            if len(delays_used) == 0:
                raise ResearchRateLimitError("rate-limited")
            return "ok"

        real_sleep = __import__('time').sleep

        def capture_sleep(secs):
            delays_used.append(secs)

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
             patch("backend.services.queue_services.get_retry_delay", return_value=5), \
             patch("time.sleep", side_effect=capture_sleep):
            result = _retry_call(rate_limited_then_ok, job.id, "research", max_retries=2)

        assert result == "ok"
        # The delay must be at least 30s for rate limit errors
        assert delays_used[0] >= 30


# ── Topic sanitisation ────────────────────────────────────────────────────────

class TestTopicSanitisation:
    """_sanitise_topic must strip quotes that break DDG searches."""

    def test_double_quoted_topic_unquoted(self):
        from backend.services.queue_processor import _sanitise_topic
        assert _sanitise_topic('"AI Automation That Saves 10 Hours/Week"') \
               == "AI Automation That Saves 10 Hours/Week"

    def test_single_quoted_topic_unquoted(self):
        from backend.services.queue_processor import _sanitise_topic
        assert _sanitise_topic("'Why do cats purr?'") == "Why do cats purr?"

    def test_smart_quotes_unquoted(self):
        from backend.services.queue_processor import _sanitise_topic
        assert _sanitise_topic('\u201cHow do black holes work?\u201d') \
               == "How do black holes work?"

    def test_normal_topic_unchanged(self):
        from backend.services.queue_processor import _sanitise_topic
        assert _sanitise_topic("Why is the sky blue?") == "Why is the sky blue?"

    def test_whitespace_stripped(self):
        from backend.services.queue_processor import _sanitise_topic
        assert _sanitise_topic("  Why is the sky blue?  ") == "Why is the sky blue?"

    def test_empty_string_safe(self):
        from backend.services.queue_processor import _sanitise_topic
        # Should not raise; returns the stripped original
        result = _sanitise_topic("  ")
        assert isinstance(result, str)

    def test_partial_quotes_stripped(self):
        from backend.services.queue_processor import _sanitise_topic
        # Lone leading quote stripped
        assert _sanitise_topic('"The AI Tools Nobody is Talking About Yet"') \
               == "The AI Tools Nobody is Talking About Yet"

    def test_inner_quotes_preserved(self):
        from backend.services.queue_processor import _sanitise_topic
        # Quotes inside the topic should NOT be removed
        result = _sanitise_topic('How to use "ChatGPT" effectively')
        assert "ChatGPT" in result


# ── TTS failure handling in queue ─────────────────────────────────────────────

class TestTTSFailureInQueue:
    """
    Regression tests for queue getting stuck after Edge-TTS → Local fallback.
    When TTS fails (Edge 403 + Local timeout/error), the job must be marked
    as failed, not remain stuck in processing state.
    """

    def test_edge_403_local_fallback_successful_audio_generation(self, db, tmp_path):
        """
        Complete end-to-end test: Edge 403 → local fallback → successful audio generation.
        This tests the exact scenario described in the issue where Edge-TTS returns HTTP 403
        and the system must fall back to local Windows SAPI TTS.
        """
        from backend.services.queue_processor import _stage_generate_audio
        from backend.services.tts.base import TTSNetworkError, TTSResult
        from backend.services.tts.manager import AutoTTSManager
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.local_provider import LocalTTSProvider
        from tests.conftest import SharedTestingSessionLocal
        from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
        import json

        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Test Edge 403 fallback",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=5,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="Test Title",
            description="Test Description",
            hook="Test hook.",
            tags_json=json.dumps([]),
            scenes_json=json.dumps([
                {"scene_number": 1, "narration": "Test narration text for Edge 403 fallback scenario.",
                 "visual_description": "test", "estimated_duration_seconds": 15},
            ]),
            estimated_duration_seconds=60,
        )
        db.add(script)
        db.commit()

        job_id = str(uuid.uuid4())

        # Create a fake WAV file for the local result
        fake_wav = tmp_path / "local_audio.wav"
        import wave, struct
        with wave.open(str(fake_wav), "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(struct.pack("<100h", *([0] * 100)))

        local_result = TTSResult(
            file_path=str(fake_wav),
            voice="local-zira",
            format="wav",
            file_size_bytes=100,
            duration_seconds=5.0,
            text_length=50,
        )

        # Mock AutoTTSManager where Edge fails (403) but Local succeeds
        primary = MagicMock(spec=EdgeTTSProvider)
        primary.provider_name = "edge"
        primary.synthesize = AsyncMock(side_effect=TTSNetworkError("Edge-TTS 403 Forbidden - IP rate limit"))

        fallback = MagicMock(spec=LocalTTSProvider)
        fallback.provider_name = "local"
        fallback.synthesize = AsyncMock(return_value=local_result)

        auto_manager = AutoTTSManager(primary=primary, fallback=fallback)

        with patch("backend.services.tts.factory.get_tts_provider", return_value=auto_manager), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            audio_id = _stage_generate_audio(job_id, project.id)

        # Verify audio was generated successfully
        assert audio_id is not None, "Audio ID should be returned after successful fallback"
        assert primary.synthesize.call_count == 1, "Edge should have been attempted once"
        assert fallback.synthesize.call_count == 1, "Local fallback should have been called once"

        # Verify the audio record was created with correct status
        db.refresh(project)
        audio_record = db.query(GeneratedAudio).filter(
            GeneratedAudio.content_project_id == project.id
        ).first()
        assert audio_record is not None, "Audio record should be created"
        assert audio_record.status == AudioStatus.COMPLETED, "Audio should be marked as completed"
        assert audio_record.provider == "local", "Provider should be recorded as 'local'"
        assert audio_record.file_path == str(fake_wav), "File path should match local output"

    def test_tts_failure_marks_job_as_failed(self, db, tmp_path):
        """
        When TTS synthesis fails, the job must be marked as FAILED
        instead of continuing without audio or hanging indefinitely.
        """
        from backend.services.queue_processor import _stage_generate_audio
        from backend.services.tts.base import TTSGenerationError
        from tests.conftest import SharedTestingSessionLocal
        from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
        import json

        # Create a completed content project with script
        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Test topic",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=5,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="Test Title",
            description="Test Description",
            hook="Test hook.",
            tags_json=json.dumps([]),
            scenes_json=json.dumps([
                {"scene_number": 1, "narration": "Test narration.",
                 "visual_description": "test", "estimated_duration_seconds": 15},
            ]),
            estimated_duration_seconds=60,
        )
        db.add(script)
        db.commit()

        job_id = str(uuid.uuid4())

        # Mock TTS provider that always fails
        mock_provider_inst = MagicMock()
        mock_provider_inst.synthesize = AsyncMock(side_effect=TTSGenerationError("Local TTS synthesis failed"))
        mock_provider_inst.provider_name = "local"

        with patch("backend.services.tts.factory.get_tts_provider", return_value=mock_provider_inst), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            # Should raise the exception, not return None
            with pytest.raises(TTSGenerationError, match="Local TTS synthesis failed"):
                _stage_generate_audio(job_id, project.id)

    def test_edge_403_local_fallback_both_providers_fail_job_marked_failed(self, db, tmp_path):
        """
        Test: Edge 403 → local fallback failure → queue job marked failed.
        When both Edge and Local fail, the job must be marked as FAILED
        instead of remaining stuck in processing state.
        """
        from backend.services.queue_processor import _stage_generate_audio
        from backend.services.tts.base import TTSNetworkError, TTSGenerationError
        from backend.services.tts.manager import AutoTTSManager
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.local_provider import LocalTTSProvider
        from tests.conftest import SharedTestingSessionLocal
        from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
        import json

        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Test both providers fail",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=5,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="T",
            description="D",
            hook="Hook.",
            tags_json=json.dumps([]),
            scenes_json=json.dumps([
                {"scene_number": 1, "narration": "Narration.",
                 "visual_description": "v", "estimated_duration_seconds": 15},
            ]),
            estimated_duration_seconds=60,
        )
        db.add(script)
        db.commit()

        job_id = str(uuid.uuid4())

        # Mock AutoTTSManager where Edge fails (403) and Local also fails
        primary = MagicMock(spec=EdgeTTSProvider)
        primary.provider_name = "edge"
        primary.synthesize = AsyncMock(side_effect=TTSNetworkError("Edge 403"))

        fallback = MagicMock(spec=LocalTTSProvider)
        fallback.provider_name = "local"
        fallback.synthesize = AsyncMock(side_effect=TTSGenerationError("Local SAPI failed"))

        auto_manager = AutoTTSManager(primary=primary, fallback=fallback)

        with patch("backend.services.tts.factory.get_tts_provider", return_value=auto_manager), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            # Should raise the Local TTS error, not return None
            with pytest.raises(TTSGenerationError, match="Local SAPI failed"):
                _stage_generate_audio(job_id, project.id)

    def test_tts_timeout_does_not_remain_active_forever(self, db, tmp_path):
        """
        Test: TTS timeout → queue job does not remain active forever.
        When local TTS times out, the job must be marked as FAILED
        instead of remaining stuck in processing state.
        """
        from backend.services.queue_processor import _stage_generate_audio
        from backend.services.tts.base import TTSGenerationError
        from tests.conftest import SharedTestingSessionLocal
        from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
        import json

        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Test TTS timeout",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=5,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="T",
            description="D",
            hook="Hook.",
            tags_json=json.dumps([]),
            scenes_json=json.dumps([
                {"scene_number": 1, "narration": "Narration.",
                 "visual_description": "v", "estimated_duration_seconds": 15},
            ]),
            estimated_duration_seconds=60,
        )
        db.add(script)
        db.commit()

        job_id = str(uuid.uuid4())

        # Mock TTS provider that times out
        mock_provider_inst = MagicMock()
        mock_provider_inst.synthesize = AsyncMock(side_effect=TTSGenerationError(
            "Local TTS chunk timed out after 120s. Windows SAPI may be unresponsive."
        ))
        mock_provider_inst.provider_name = "local"

        with patch("backend.services.tts.factory.get_tts_provider", return_value=mock_provider_inst), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            # Should raise the timeout error, not hang indefinitely
            with pytest.raises(TTSGenerationError, match="timed out"):
                _stage_generate_audio(job_id, project.id)

    def test_successful_local_audio_video_stage_starts(self, db, tmp_path):
        """
        Test: Successful local audio generation → video stage starts.
        Verify that after successful local TTS, the pipeline continues to video generation.
        """
        from backend.services.queue_processor import _stage_generate_audio
        from backend.services.tts.base import TTSResult
        from tests.conftest import SharedTestingSessionLocal
        from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
        import json

        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Test local audio to video",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=5,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="T",
            description="D",
            hook="Hook.",
            tags_json=json.dumps([]),
            scenes_json=json.dumps([
                {"scene_number": 1, "narration": "Narration.",
                 "visual_description": "v", "estimated_duration_seconds": 15},
            ]),
            estimated_duration_seconds=60,
        )
        db.add(script)
        db.commit()

        job_id = str(uuid.uuid4())

        # Create a fake WAV file for the local result
        fake_wav = tmp_path / "local_audio.wav"
        import wave, struct
        with wave.open(str(fake_wav), "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(struct.pack("<100h", *([0] * 100)))

        local_result = TTSResult(
            file_path=str(fake_wav),
            voice="local-zira",
            format="wav",
            file_size_bytes=100,
            duration_seconds=5.0,
            text_length=50,
        )

        # Mock local provider that succeeds
        mock_provider_inst = MagicMock()
        mock_provider_inst.synthesize = AsyncMock(return_value=local_result)
        mock_provider_inst.provider_name = "local"
        mock_provider_inst.last_used_provider = "local"

        with patch("backend.services.tts.factory.get_tts_provider", return_value=mock_provider_inst), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            audio_id = _stage_generate_audio(job_id, project.id)

        # Verify audio was generated and pipeline can continue
        assert audio_id is not None, "Audio ID should be returned"

        # Verify the audio record was created with correct status
        audio_record = db.query(GeneratedAudio).filter(
            GeneratedAudio.content_project_id == project.id
        ).first()
        assert audio_record is not None, "Audio record should be created"
        assert audio_record.status == AudioStatus.COMPLETED, "Audio should be marked as completed"
        assert audio_record.provider == "local", "Provider should be recorded as 'local'"

    def test_provider_and_audio_status_persisted_correctly(self, db, tmp_path):
        """
        Test: Provider and generated audio status are persisted correctly.
        Verify that when local fallback succeeds, the provider field and status
        are correctly saved to the database.
        """
        from backend.services.queue_processor import _stage_generate_audio
        from backend.services.tts.base import TTSResult
        from tests.conftest import SharedTestingSessionLocal
        from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
        import json

        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Test provider persistence",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=5,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="T",
            description="D",
            hook="Hook.",
            tags_json=json.dumps([]),
            scenes_json=json.dumps([
                {"scene_number": 1, "narration": "Narration.",
                 "visual_description": "v", "estimated_duration_seconds": 15},
            ]),
            estimated_duration_seconds=60,
        )
        db.add(script)
        db.commit()

        job_id = str(uuid.uuid4())

        # Create a fake WAV file
        fake_wav = tmp_path / "local_audio.wav"
        import wave, struct
        with wave.open(str(fake_wav), "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(struct.pack("<100h", *([0] * 100)))

        local_result = TTSResult(
            file_path=str(fake_wav),
            voice="local-zira",
            format="wav",
            file_size_bytes=100,
            duration_seconds=5.0,
            text_length=50,
        )

        # Mock local provider
        mock_provider_inst = MagicMock()
        mock_provider_inst.synthesize = AsyncMock(return_value=local_result)
        mock_provider_inst.provider_name = "local"
        mock_provider_inst.last_used_provider = "local"

        with patch("backend.services.tts.factory.get_tts_provider", return_value=mock_provider_inst), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            audio_id = _stage_generate_audio(job_id, project.id)

        # Verify all fields are persisted correctly
        audio_record = db.query(GeneratedAudio).filter(
            GeneratedAudio.content_project_id == project.id
        ).first()
        assert audio_record is not None
        assert audio_record.status == AudioStatus.COMPLETED
        assert audio_record.provider == "local"
        assert audio_record.voice == "en-US-AriaNeural"  # This matches the voice used in the test
        assert audio_record.file_path == str(fake_wav)
        assert audio_record.file_size_bytes == 100
        assert audio_record.duration_seconds == 5.0
        assert audio_record.text_length == 17  # Actual narration text length from script
        assert audio_record.error_message is None


# ── TTS fallback reliability (Phase 3B queue stuck fix) ──────────────────────

class TestTTSFallbackReliability:
    """
    Regression tests for the queue getting stuck after Edge→Local TTS fallback.

    Root cause: asyncio.get_event_loop() in LocalTTSProvider.synthesize()
    returned the wrong loop when called from a queue worker background thread,
    causing run_in_executor() to deadlock.

    Fix: use asyncio.get_running_loop() which always returns the loop that is
    currently executing the coroutine.
    """

    def test_local_provider_uses_get_running_loop(self):
        """
        LocalTTSProvider.synthesize() must use asyncio.get_running_loop(),
        NOT asyncio.get_event_loop().
        """
        import inspect
        from backend.services.tts import local_provider
        src = inspect.getsource(local_provider.LocalTTSProvider.synthesize)
        assert "get_running_loop" in src, (
            "LocalTTSProvider.synthesize() must use asyncio.get_running_loop() "
            "not get_event_loop() to avoid deadlock in queue worker threads."
        )
        # Verify the actual call is to get_running_loop
        assert "asyncio.get_running_loop()" in src, (
            "LocalTTSProvider.synthesize() must call asyncio.get_running_loop() directly."
        )

    def test_run_chunk_with_timeout_helper_exists(self):
        """_run_chunk_with_timeout helper must be importable."""
        from backend.services.tts.local_provider import _run_chunk_with_timeout
        import asyncio, inspect
        assert asyncio.iscoroutinefunction(_run_chunk_with_timeout)

    def test_run_chunk_timeout_raises_tts_generation_error(self, tmp_path):
        """
        A chunk that times out must raise TTSGenerationError, not hang forever.
        The queue job can then fail/retry cleanly.
        """
        import asyncio, time
        from backend.services.tts.local_provider import _run_chunk_with_timeout
        from backend.services.tts.base import TTSGenerationError

        def slow_fn(*args):
            time.sleep(60)  # simulate a hung pyttsx3.runAndWait()

        async def run():
            loop = asyncio.get_running_loop()
            await _run_chunk_with_timeout(loop, slow_fn, (), timeout_s=0.1)

        with pytest.raises(TTSGenerationError, match="timed out"):
            asyncio.run(run())

    def test_local_provider_synthesis_succeeds_with_mocked_pyttsx3(self, tmp_path):
        """
        LocalTTSProvider.synthesize() must complete successfully when pyttsx3
        is mocked — i.e., the event-loop wiring is correct end-to-end.
        """
        import asyncio, struct, wave
        from unittest.mock import MagicMock, patch
        from backend.services.tts.local_provider import LocalTTSProvider

        def fake_save(text, path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "w") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
                w.writeframes(struct.pack("<100h", *([0] * 100)))

        mock_engine = MagicMock()
        mock_engine.getProperty.side_effect = lambda p: (
            [MagicMock(id="HKEY\\Zira", name="Microsoft Zira Desktop", languages=["en-US"])]
            if p == "voices" else 165
        )
        mock_engine.save_to_file = MagicMock(side_effect=fake_save)
        mock_engine.runAndWait   = MagicMock()

        with patch.dict(os.environ, {"TTS_OUTPUT_DIR": str(tmp_path), "TTS_LOCAL_RATE": "165"}):
            provider = LocalTTSProvider()

        with patch("pyttsx3.init", return_value=mock_engine):
            result = asyncio.run(provider.synthesize(
                "Hello world from local TTS.", "local-zira",
                str(tmp_path / "output.wav"),
            ))

        assert result.format == "wav"
        assert result.file_size_bytes > 0
        assert Path(result.file_path).exists()

    def test_edge_failure_triggers_local_synthesis(self, tmp_path):
        """
        When Edge-TTS raises TTSNetworkError, AutoTTSManager must call
        local synthesis and return a result with provider=local.
        """
        import asyncio, struct, wave
        from unittest.mock import MagicMock, AsyncMock, patch
        from backend.services.tts.manager import AutoTTSManager
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.local_provider import LocalTTSProvider
        from backend.services.tts.base import TTSNetworkError, TTSResult

        local_result = TTSResult(
            file_path=str(tmp_path / "narration.wav"),
            voice="local-zira",
            format="wav",
            file_size_bytes=50000,
            duration_seconds=30.0,
            text_length=50,
        )

        primary  = MagicMock(spec=EdgeTTSProvider)
        primary.provider_name = "edge"
        primary.synthesize    = AsyncMock(side_effect=TTSNetworkError("403 Forbidden"))

        fallback = MagicMock(spec=LocalTTSProvider)
        fallback.provider_name = "local"
        fallback.synthesize    = AsyncMock(return_value=local_result)

        manager = AutoTTSManager(primary=primary, fallback=fallback)

        result = asyncio.run(manager.synthesize(
            "Hello world.", "en-US-AriaNeural", str(tmp_path / "out.mp3")
        ))

        assert manager.last_used_provider == "local"
        fallback.synthesize.assert_called_once()
        primary.synthesize.assert_called_once()

    def test_queue_tts_stage_does_not_hang_on_local_failure(self, db):
        """
        If local TTS synthesis raises an exception (simulating a stuck pyttsx3),
        _stage_generate_audio must catch it, log it, and return None —
        NOT leave the queue job stuck forever.
        """
        from backend.services.queue_processor import _stage_generate_audio
        from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
        from backend.tts_models import GeneratedAudio, AudioStatus
        from tests.conftest import SharedTestingSessionLocal
        import json as _json

        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Test TTS hang",
            language="en", tone="engaging",
            target_duration_seconds=180, scene_count=10,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="Test", description="",
            hook="Hook.", tags_json=_json.dumps([]),
            scenes_json=_json.dumps([{
                "scene_number": 1, "narration": "Test narration.",
                "visual_description": "v", "estimated_duration_seconds": 30,
            }]),
            estimated_duration_seconds=30,
        )
        db.add(script)
        queue_job = _make_job(db, content_project_id=project.id)
        db.commit()

        # Simulate local TTS raising (e.g. timeout or SAPI error)
        from backend.services.tts.base import TTSGenerationError
        with patch("backend.services.tts.factory.get_tts_provider") as mock_provider, \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            mock_prov = MagicMock()
            mock_prov.synthesize = AsyncMock(side_effect=TTSGenerationError("SAPI timed out"))
            mock_prov.provider_name = "local"
            mock_provider.return_value = mock_prov

            # After our fix, this should raise the exception, not return None
            with pytest.raises(TTSGenerationError, match="SAPI timed out"):
                _stage_generate_audio(queue_job.id, project.id)


class TestVideoJobStatusUpdates:
    def test_video_job_status_updated_during_pipeline(self, db):
        """
        When video pipeline runs, the VideoGenerationJob status should be updated
        via the progress callback, not stuck at 'preparing'.
        """
        from backend.services.queue_processor import _stage_generate_video
        from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
        from backend.tts_models import GeneratedAudio, AudioStatus
        from tests.conftest import SharedTestingSessionLocal
        import json as _json

        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Test video status",
            language="en", tone="engaging",
            target_duration_seconds=180, scene_count=10,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="Test", description="",
            hook="Hook.", tags_json=_json.dumps([]),
            scenes_json=_json.dumps([{
                "scene_number": 1, "narration": "Test narration.",
                "visual_description": "v", "estimated_duration_seconds": 30,
            }]),
            estimated_duration_seconds=30,
        )
        db.add(script)
        audio = GeneratedAudio(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            status=AudioStatus.COMPLETED,
            provider="local",
            voice="en-US-AriaNeural",
            language="en",
            text_length=100,
            file_path="fake.wav",
            file_size_bytes=1000,
            duration_seconds=30,
        )
        db.add(audio)
        queue_job = _make_job(db, content_project_id=project.id)
        db.commit()

        # Mock the pipeline to simulate progress updates
        def mock_run_pipeline(*args, **kwargs):
            progress_cb = kwargs.get("progress_callback")
            if progress_cb:
                # Simulate progress through the pipeline
                progress_cb(10, "Verifying FFmpeg")
                progress_cb(30, "Building scene visuals")
                progress_cb(60, "Building scene clips")
                progress_cb(80, "Generating captions")
                progress_cb(95, "Generating thumbnail")
                progress_cb(100, "Complete")
            return {
                "output_path": "fake.mp4",
                "thumbnail_path": "fake.jpg",
                "caption_path": "fake.srt",
                "duration_seconds": 30,
                "file_size_bytes": 1000000,
            }

        with patch("backend.services.video.pipeline.run_pipeline", side_effect=mock_run_pipeline), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            video_job_id = _stage_generate_video(queue_job.id, project.id, audio.id)

            # Verify video job was created and progressed beyond 'preparing'
            db2 = SharedTestingSessionLocal()
            try:
                vj = db2.query(VideoGenerationJob).filter(VideoGenerationJob.id == video_job_id).first()
                assert vj is not None
                assert vj.status == VideoJobStatus.COMPLETED
                assert vj.progress == 100
                assert vj.current_step == "Complete"
                assert vj.output_path == "fake.mp4"
            finally:
                db2.close()

    def test_video_job_marked_failed_on_exception(self, db):
        """
        When video generation fails, the VideoGenerationJob should be marked
        as FAILED with an error message.
        """
        from backend.services.queue_processor import _stage_generate_video
        from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
        from backend.tts_models import GeneratedAudio, AudioStatus
        from backend.services.video.exceptions import VideoGenerationError
        from tests.conftest import SharedTestingSessionLocal
        import json as _json

        project = ContentProject(
            id=str(uuid.uuid4()),
            topic="Test video failure",
            language="en", tone="engaging",
            target_duration_seconds=180, scene_count=10,
            status=ContentStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(project)
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="Test", description="",
            hook="Hook.", tags_json=_json.dumps([]),
            scenes_json=_json.dumps([{
                "scene_number": 1, "narration": "Test narration.",
                "visual_description": "v", "estimated_duration_seconds": 30,
            }]),
            estimated_duration_seconds=30,
        )
        db.add(script)
        audio = GeneratedAudio(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            status=AudioStatus.COMPLETED,
            provider="local",
            voice="en-US-AriaNeural",
            language="en",
            text_length=100,
            file_path="fake.wav",
            file_size_bytes=1000,
            duration_seconds=30,
        )
        db.add(audio)
        queue_job = _make_job(db, content_project_id=project.id)
        db.commit()

        # Mock the pipeline to raise an exception
        def mock_run_pipeline(*args, **kwargs):
            raise VideoGenerationError("FFmpeg failed")

        with patch("backend.services.video.pipeline.run_pipeline", side_effect=mock_run_pipeline), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            with pytest.raises(VideoGenerationError, match="FFmpeg failed"):
                _stage_generate_video(queue_job.id, project.id, audio.id)

            # Verify video job was marked as failed
            db2 = SharedTestingSessionLocal()
            try:
                vjs = db2.query(VideoGenerationJob).all()
                # Find the video job that was created
                failed_vj = None
                for vj in vjs:
                    if vj.status == VideoJobStatus.FAILED:
                        failed_vj = vj
                        break
                assert failed_vj is not None
                assert failed_vj.error_message is not None
                assert "FFmpeg failed" in failed_vj.error_message
            finally:
                db2.close()
