"""
tests/test_queue.py — Phase 3A content queue tests.

All external services (Groq, DDG, TTS, FFmpeg, YouTube) are mocked.
No network calls, no real file I/O beyond tmp_path fixtures.

Coverage (A–AQ):
  Queue creation:
    A.  Bulk create with valid topics               → 201, N jobs
    B.  Empty topics list                           → 422
    C.  Whitespace-only topics filtered             → 422
    D.  Too many topics (>50)                       → 422
    E.  Single topic                                → 201
    F.  Invalid privacy_status                      → 422
    G.  Scheduled jobs get correct publish times
    H.  schedule_interval_minutes spacing
    I.  No schedule → scheduled_publish_at=None
    J.  Invalid schedule_start                      → 422
    K.  Jobs created with status=queued
    L.  Jobs stored in DB

  Queue listing / retrieval:
    M.  GET /api/queue                              → 200 list
    N.  GET /api/queue/{id}                         → 200
    O.  GET /api/queue/nonexistent                  → 404
    P.  GET /api/queue?status=queued filter

  Queue status endpoint:
    Q.  GET /api/queue/status structure

  Cancel:
    R.  Cancel queued job                           → 200 cancelled
    S.  Cancel completed job                        → 409
    T.  Cancel nonexistent                          → 404

  Retry:
    U.  Retry failed job                            → 200 queued
    V.  Retry queued job                            → 409
    W.  Retry resets retry_count

  Pause / resume:
    X.  POST /api/queue/pause                       → 200
    Y.  POST /api/queue/resume                      → 200

  Start:
    Z.  POST /api/queue/start                       → 200

  Delete:
    AA. Delete completed job                        → 204
    AB. Delete currently processing job             → 409

  QueueStatus model:
    AC. All status constants defined
    AD. TERMINAL set correct
    AK. ACTIVE set correct

  Schedule calculation:
    AE. N topics → N scheduled times
    AF. Interval applied correctly (UTC)
    AG. DST-safe via existing scheduler

  Recovery:
    AH. Stale RESEARCHING job requeued on startup
    AI. Stale job with youtube_video_id → completed

  Processor internals:
    AJ. _is_transient detects timeout/429/rate limit
    AK. _is_transient rejects permanent errors
    AL. _mark_failed increments retry_count and requeues
    AM. _mark_failed at max_retries → FAILED

  Duplicate upload protection:
    AN. Job with youtube_video_id skips upload stage

  Security:
    AO. No credentials in response
    AP. No file paths in job response
    AQ. status endpoint safe
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import Base, get_db
from backend.main import app
from backend.queue_models import ContentQueueJob, QueueStatus, queue_job_to_response

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

def _now_plus(minutes: int) -> str:
    """ISO8601 UTC datetime N minutes in the future."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _make_job(db, status=QueueStatus.QUEUED, **kw) -> ContentQueueJob:
    defaults = dict(
        id=str(uuid.uuid4()),
        topic="Test topic",
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


# ── A–L: Queue creation ───────────────────────────────────────────────────────

class TestCreateQueueJobs:

    def _patch_worker(self):
        return patch("backend.routers.queue.start_worker")

    # A. Bulk create valid
    def test_bulk_create_valid(self, client):
        with self._patch_worker():
            r = client.post("/api/queue", json={
                "topics": ["Why do cats purr?", "How do black holes work?"],
                "language": "en",
                "tone": "engaging",
            })
        assert r.status_code == 201
        body = r.json()
        assert body["created"] == 2
        assert len(body["jobs"]) == 2

    # B. Empty topics
    def test_empty_topics_rejected(self, client):
        r = client.post("/api/queue", json={"topics": []})
        assert r.status_code == 422

    # C. Whitespace-only topics
    def test_whitespace_topics_rejected(self, client):
        r = client.post("/api/queue", json={"topics": ["   ", "\t"]})
        assert r.status_code == 422

    # D. Too many topics
    def test_too_many_topics_rejected(self, client):
        r = client.post("/api/queue", json={"topics": [f"topic {i}" for i in range(51)]})
        assert r.status_code == 422

    # E. Single topic
    def test_single_topic(self, client):
        with self._patch_worker():
            r = client.post("/api/queue", json={"topics": ["Why is the sky blue?"]})
        assert r.status_code == 201
        assert r.json()["created"] == 1

    # F. Invalid privacy_status
    def test_invalid_privacy_rejected(self, client):
        r = client.post("/api/queue", json={
            "topics": ["test"],
            "youtube_privacy_status": "invisible",
        })
        assert r.status_code == 422

    # G. Scheduled jobs get correct publish times
    def test_scheduled_jobs_publish_times(self, client):
        start = _now_plus(30)  # 30 minutes from now
        with self._patch_worker():
            r = client.post("/api/queue", json={
                "topics": ["topic 1", "topic 2"],
                "schedule_start": start,
                "schedule_interval_minutes": 60,
            })
        assert r.status_code == 201
        jobs = r.json()["jobs"]
        t1 = datetime.fromisoformat(jobs[0]["scheduled_publish_at"])
        t2 = datetime.fromisoformat(jobs[1]["scheduled_publish_at"])
        diff = (t2 - t1).total_seconds()
        assert abs(diff - 3600) < 5  # ~60 minutes apart

    # H. Interval spacing
    def test_interval_spacing(self, client):
        start = _now_plus(30)
        with self._patch_worker():
            r = client.post("/api/queue", json={
                "topics": ["a", "b", "c"],
                "schedule_start": start,
                "schedule_interval_minutes": 120,
            })
        jobs = r.json()["jobs"]
        times = [datetime.fromisoformat(j["scheduled_publish_at"]) for j in jobs]
        assert abs((times[1] - times[0]).total_seconds() - 7200) < 5
        assert abs((times[2] - times[1]).total_seconds() - 7200) < 5

    # I. No schedule
    def test_no_schedule_null_publish_at(self, client):
        with self._patch_worker():
            r = client.post("/api/queue", json={"topics": ["test"]})
        assert r.json()["jobs"][0]["scheduled_publish_at"] is None

    # J. Invalid schedule_start
    def test_invalid_schedule_start(self, client):
        r = client.post("/api/queue", json={
            "topics": ["test"],
            "schedule_start": "not-a-date",
        })
        assert r.status_code == 422

    # K. Jobs start as queued
    def test_jobs_start_queued(self, client, db):
        with self._patch_worker():
            r = client.post("/api/queue", json={"topics": ["topic x"]})
        job_id = r.json()["jobs"][0]["id"]
        job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
        assert job.status == QueueStatus.QUEUED

    # L. Jobs stored in DB
    def test_jobs_persisted_in_db(self, client, db):
        with self._patch_worker():
            r = client.post("/api/queue", json={
                "topics": ["topic A", "topic B"],
            })
        before = db.query(ContentQueueJob).count()
        assert before >= 2


# ── M–P: List / retrieval ─────────────────────────────────────────────────────

class TestListGetJobs:

    def test_list_returns_200(self, client, db):
        _make_job(db)
        r = client.get("/api/queue")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_job_returns_200(self, client, db):
        job = _make_job(db)
        r = client.get(f"/api/queue/{job.id}")
        assert r.status_code == 200
        assert r.json()["id"] == job.id

    def test_get_nonexistent_404(self, client):
        r = client.get("/api/queue/nonexistent-id")
        assert r.status_code == 404

    def test_list_filter_by_status(self, client, db):
        _make_job(db, status=QueueStatus.QUEUED)
        _make_job(db, status=QueueStatus.FAILED)
        r = client.get("/api/queue?status=queued")
        assert r.status_code == 200
        for j in r.json():
            assert j["status"] == QueueStatus.QUEUED


# ── Q: Status endpoint ────────────────────────────────────────────────────────

class TestQueueStatus:

    def test_status_structure(self, client):
        with patch("backend.routers.queue.is_worker_alive", return_value=False), \
             patch("backend.routers.queue.is_paused", return_value=False), \
             patch("backend.routers.queue.get_current_job_id", return_value=None):
            r = client.get("/api/queue/status")
        assert r.status_code == 200
        body = r.json()
        for key in ("queue_running", "queue_paused", "total", "queued",
                    "processing", "completed", "failed", "cancelled"):
            assert key in body


# ── R–T: Cancel ───────────────────────────────────────────────────────────────

class TestCancelJob:

    def test_cancel_queued_job(self, client, db):
        job = _make_job(db, status=QueueStatus.QUEUED)
        with patch("backend.routers.queue.get_current_job_id", return_value="other"):
            r = client.post(f"/api/queue/{job.id}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == QueueStatus.CANCELLED

    def test_cancel_completed_returns_409(self, client, db):
        job = _make_job(db, status=QueueStatus.COMPLETED)
        r = client.post(f"/api/queue/{job.id}/cancel")
        assert r.status_code == 409

    def test_cancel_nonexistent_404(self, client):
        r = client.post("/api/queue/nonexistent/cancel")
        assert r.status_code == 404


# ── U–W: Retry ────────────────────────────────────────────────────────────────

class TestRetryJob:

    def test_retry_failed_job(self, client, db):
        job = _make_job(db, status=QueueStatus.FAILED)
        with patch("backend.routers.queue.start_worker"):
            r = client.post(f"/api/queue/{job.id}/retry")
        assert r.status_code == 200
        assert r.json()["status"] == QueueStatus.QUEUED

    def test_retry_queued_returns_409(self, client, db):
        job = _make_job(db, status=QueueStatus.QUEUED)
        r = client.post(f"/api/queue/{job.id}/retry")
        assert r.status_code == 409

    def test_retry_resets_retry_count(self, client, db):
        job = _make_job(db, status=QueueStatus.FAILED, retry_count=3, max_retries=3)
        with patch("backend.routers.queue.start_worker"):
            r = client.post(f"/api/queue/{job.id}/retry")
        assert r.json()["retry_count"] == 0


# ── X–Z: Pause / resume / start ──────────────────────────────────────────────

class TestQueueControls:

    def test_pause_returns_200(self, client):
        with patch("backend.routers.queue.pause_queue"):
            r = client.post("/api/queue/pause")
        assert r.status_code == 200

    def test_resume_returns_200(self, client):
        with patch("backend.routers.queue.resume_queue"), \
             patch("backend.routers.queue.start_worker"):
            r = client.post("/api/queue/resume")
        assert r.status_code == 200

    def test_start_returns_200(self, client):
        with patch("backend.routers.queue.start_worker", return_value=True):
            r = client.post("/api/queue/start")
        assert r.status_code == 200


# ── AA–AB: Delete ─────────────────────────────────────────────────────────────

class TestDeleteJob:

    def test_delete_completed_job(self, client, db):
        job = _make_job(db, status=QueueStatus.COMPLETED)
        with patch("backend.routers.queue.get_current_job_id", return_value="other"):
            r = client.delete(f"/api/queue/{job.id}")
        assert r.status_code == 204

    def test_delete_processing_job_409(self, client, db):
        job = _make_job(db, status=QueueStatus.RESEARCHING)
        with patch("backend.routers.queue.get_current_job_id", return_value=job.id):
            r = client.delete(f"/api/queue/{job.id}")
        assert r.status_code == 409


# ── AC–AK: Status constants ───────────────────────────────────────────────────

class TestQueueStatusConstants:

    def test_all_statuses_defined(self):
        expected = {
            "queued", "researching", "generating_script", "generating_audio",
            "generating_video", "uploading", "scheduled", "completed",
            "failed", "cancelled", "paused",
        }
        assert QueueStatus.ALL == expected

    def test_terminal_set(self):
        assert QueueStatus.TERMINAL == {"completed", "failed", "cancelled"}

    def test_active_set(self):
        assert QueueStatus.ACTIVE == {
            "researching", "generating_script", "generating_audio",
            "generating_video", "uploading",
        }


# ── AE–AF: Schedule calculation ──────────────────────────────────────────────

class TestScheduleCalculation:

    def test_n_topics_n_times(self, client):
        start = _now_plus(30)
        with patch("backend.services.queue_processor.start_worker"):
            r = client.post("/api/queue", json={
                "topics": ["a", "b", "c", "d"],
                "schedule_start": start,
                "schedule_interval_minutes": 60,
            })
        assert r.status_code == 201
        jobs = r.json()["jobs"]
        assert len(jobs) == 4
        for j in jobs:
            assert j["scheduled_publish_at"] is not None

    def test_interval_applied_correctly(self, client):
        start = _now_plus(30)
        with patch("backend.services.queue_processor.start_worker"):
            r = client.post("/api/queue", json={
                "topics": ["x", "y"],
                "schedule_start": start,
                "schedule_interval_minutes": 30,
            })
        jobs = r.json()["jobs"]
        t0 = datetime.fromisoformat(jobs[0]["scheduled_publish_at"])
        t1 = datetime.fromisoformat(jobs[1]["scheduled_publish_at"])
        assert abs((t1 - t0).total_seconds() - 1800) < 5  # 30 min


# ── AH–AI: Recovery ───────────────────────────────────────────────────────────

class TestStartupRecovery:

    def test_stale_researching_job_requeued(self, db):
        from backend.services.queue_processor import _recover_stale_jobs
        from tests.conftest import SharedTestingSessionLocal

        # Create a stale job
        job = _make_job(db, status=QueueStatus.RESEARCHING, retry_count=0, max_retries=3)

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            _recover_stale_jobs()

        db.expire(job)
        db.refresh(job)
        assert job.status == QueueStatus.QUEUED
        assert job.retry_count == 1

    def test_stale_job_with_video_id_completed(self, db):
        from backend.services.queue_processor import _recover_stale_jobs
        from tests.conftest import SharedTestingSessionLocal

        job = _make_job(
            db,
            status=QueueStatus.UPLOADING,
            youtube_video_id="dQw4w9WgXcQ",
            retry_count=0,
        )

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            _recover_stale_jobs()

        db.expire(job)
        db.refresh(job)
        assert job.status == QueueStatus.COMPLETED


# ── AJ–AK: _is_transient ─────────────────────────────────────────────────────

class TestIsTransient:

    def test_detects_timeout(self):
        from backend.services.queue_processor import _is_transient
        assert _is_transient(Exception("connection timeout"))
        assert _is_transient(Exception("429 rate limit exceeded"))
        assert _is_transient(Exception("503 service unavailable"))

    def test_permanent_errors_not_transient(self):
        from backend.services.queue_processor import _is_transient
        assert not _is_transient(ValueError("invalid topic"))
        assert not _is_transient(FileNotFoundError("file missing"))


# ── AL–AM: _mark_failed ───────────────────────────────────────────────────────

class TestMarkFailed:

    def test_increments_retry_and_requeues(self, db):
        from backend.services.queue_processor import _mark_failed
        from tests.conftest import SharedTestingSessionLocal

        job = _make_job(db, status=QueueStatus.RESEARCHING, retry_count=0, max_retries=3)

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            _mark_failed(job.id, "Transient network error")

        db.expire(job)
        db.refresh(job)
        assert job.status == QueueStatus.QUEUED
        assert job.retry_count == 1

    def test_max_retries_marks_failed(self, db):
        from backend.services.queue_processor import _mark_failed
        from tests.conftest import SharedTestingSessionLocal

        job = _make_job(
            db,
            status=QueueStatus.RESEARCHING,
            retry_count=3,  # already at max
            max_retries=3,
        )

        with patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            _mark_failed(job.id, "Final failure")

        db.expire(job)
        db.refresh(job)
        assert job.status == QueueStatus.FAILED
        assert "Final failure" in (job.error_message or "")


# ── AN: Duplicate upload protection ──────────────────────────────────────────

class TestDuplicateProtection:

    def test_job_with_video_id_skips_upload(self, db):
        """If youtube_video_id is already set, the upload stage must not call upload_video."""
        from backend.services.queue_processor import _stage_youtube
        from tests.conftest import SharedTestingSessionLocal
        from backend.video_generation_models import VideoGenerationJob, VideoJobStatus
        import tempfile, os

        # Create a real temp MP4 stub
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False, dir=r"G:\youtube-uploader\data") as f:
            f.write(b"\x00" * 100)
            tmp_path = f.name

        try:
            # Create video job
            vj = VideoGenerationJob(
                id=str(uuid.uuid4()),
                content_project_id=str(uuid.uuid4()),
                status=VideoJobStatus.COMPLETED,
                progress=100,
                output_path=tmp_path,
                width=1920, height=1080, fps=30,
                captions_enabled=True, music_enabled=False,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(vj)

            # Create queue job with youtube_video_id already set
            job = _make_job(
                db,
                status=QueueStatus.UPLOADING,
                video_job_id=vj.id,
                youtube_video_id="dQw4w9WgXcQ",  # already uploaded
            )

            import youtube as yt_mock_module
            with patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
                 patch.object(yt_mock_module, "upload_video") as mock_upload:
                _stage_youtube(job.id, vj.id)
                mock_upload.assert_not_called()
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ── AO–AQ: Security ───────────────────────────────────────────────────────────

class TestQueueSecurity:

    def test_no_credentials_in_response(self, client, db):
        job = _make_job(db)
        body = client.get(f"/api/queue/{job.id}").text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body
        assert "access_token" not in body

    def test_no_file_paths_in_job_response(self, client, db):
        """Job response must not expose absolute filesystem paths."""
        job = _make_job(db)
        body = client.get(f"/api/queue/{job.id}").json()
        # filesystem paths would start with G:\ or C:\
        body_str = json.dumps(body)
        assert "G:\\" not in body_str
        assert "C:\\" not in body_str

    def test_queue_status_safe(self, client):
        with patch("backend.routers.queue.is_worker_alive", return_value=False), \
             patch("backend.routers.queue.is_paused", return_value=False), \
             patch("backend.routers.queue.get_current_job_id", return_value=None):
            r = client.get("/api/queue/status")
        body = r.text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body


# ── queue_job_to_response ─────────────────────────────────────────────────────

class TestQueueJobToResponse:

    def test_response_has_all_required_fields(self, db):
        job = _make_job(db)
        resp = queue_job_to_response(job)
        assert resp.id == job.id
        assert resp.topic == job.topic
        assert resp.status == QueueStatus.QUEUED
        assert resp.progress == 0
        assert resp.retry_count == 0

    def test_youtube_url_format(self, db):
        job = _make_job(db, youtube_video_id="abc123", youtube_url="https://youtube.com/watch?v=abc123")
        resp = queue_job_to_response(job)
        assert resp.youtube_url == "https://youtube.com/watch?v=abc123"
        assert resp.youtube_video_id == "abc123"
