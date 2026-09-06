"""
tests/test_api.py — FastAPI endpoint tests (no real YouTube API calls).

Uses pytest + httpx TestClient with mocked YouTube service functions.
Tests:
  - GET /api/health
  - GET /api/auth/status (no token / with token)
  - GET /api/videos (empty list, populated list)
  - POST /api/uploads (validation: missing fields, bad privacy, past schedule)
  - GET /api/uploads/{job_id}/status (found, not found)
  - DELETE /api/uploads/{job_id}
  - PATCH /api/videos/{video_id} (title update, reschedule, clear_schedule)
  - POST /api/videos/{video_id}/cancel-schedule
  - Security: credentials/tokens never leaked in any response
"""

from __future__ import annotations

import io
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import Base, get_db
from backend.main import app
from backend.models import JobStatus, UploadJob

# ── Test database ──────────────────────────────────────────────────────────────
# Use the shared engine managed by conftest.py so both test files share the
# same StaticPool connection and the app override stays consistent.
from tests.conftest import shared_engine, SharedTestingSessionLocal

test_engine = shared_engine
TestingSessionLocal = SharedTestingSessionLocal


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# conftest.py already set this override; reassigning here keeps it pointing
# to the same shared engine (idempotent).
app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    """Recreate tables before each test for isolation."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def client():
    # raise_server_exceptions=True so we see real errors in test output
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def db():
    """Direct DB session for seeding test data — shares the same StaticPool."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def future_iso(hours: int = 48) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def make_job(db, **kwargs) -> UploadJob:
    defaults = dict(
        id             = str(uuid.uuid4()),
        filename       = "test.mp4",
        title          = "Test Video",
        description    = "A test",
        tags           = "tag1,tag2",
        category_id    = "22",
        privacy_status = "private",
        status         = JobStatus.PUBLISHED,
        progress       = 100,
        video_id       = "abc123",
        scheduled_at   = None,
        created_at     = datetime.now(timezone.utc),
        updated_at     = datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    job = UploadJob(**defaults)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ── Health ─────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_health_no_sensitive_data(self, client):
        r    = client.get("/api/health")
        body = r.text
        # Ensure no secrets leaked
        assert "client_secret" not in body
        assert "refresh_token" not in body
        assert "access_token"  not in body


# ── Auth status ────────────────────────────────────────────────────────────────

class TestAuthStatus:
    def test_no_token_returns_not_authenticated(self, client):
        # Patch Path.exists on the resolved token path so the test is
        # CWD-independent (we use Path.exists() now, not os.path.exists).
        with patch("backend.routers.auth.Path.exists", return_value=False):
            r = client.get("/api/auth/status")
        assert r.status_code == 200
        assert r.json()["authenticated"] is False

    def test_valid_token_returns_authenticated(self, client, tmp_path):
        """A readable, valid token file reports authenticated=True."""
        import json
        from unittest.mock import MagicMock

        # Write a minimal token.json to a temp path
        token_data = {
            "token": "fake_access_token",
            "refresh_token": "fake_refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "fake_client_id",
            "client_secret": "fake_client_secret",
            "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
        }
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps(token_data))

        mock_creds = MagicMock()
        mock_creds.valid = True

        with patch("backend.routers.auth._resolve_path", return_value=token_file), \
             patch("google.oauth2.credentials.Credentials.from_authorized_user_file",
                   return_value=mock_creds):
            r = client.get("/api/auth/status")

        assert r.status_code == 200
        assert r.json()["authenticated"] is True

    def test_expired_token_with_refresh_reports_authenticated(self, client, tmp_path):
        """Expired token that has a refresh_token still reports authenticated."""
        import json
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"placeholder": True}))

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "some_refresh_token"

        with patch("backend.routers.auth._resolve_path", return_value=token_file), \
             patch("google.oauth2.credentials.Credentials.from_authorized_user_file",
                   return_value=mock_creds):
            r = client.get("/api/auth/status")

        assert r.status_code == 200
        assert r.json()["authenticated"] is True

    def test_path_resolved_relative_to_project_root(self):
        """_resolve_path must return an absolute path under the project root."""
        from pathlib import Path
        from backend.routers.auth import _resolve_path, _PROJECT_ROOT

        result = _resolve_path("TOKEN_FILE", "token.json")
        assert result.is_absolute(), "Resolved path must be absolute"
        # Default should land in the project root, not the CWD
        assert result == _PROJECT_ROOT / "token.json"

    def test_absolute_env_var_used_as_is(self, tmp_path):
        """An absolute TOKEN_FILE env var is used without modification."""
        from backend.routers.auth import _resolve_path

        abs_path = str(tmp_path / "my_token.json")
        with patch.dict(os.environ, {"TOKEN_FILE": abs_path}):
            result = _resolve_path("TOKEN_FILE", "token.json")
        assert str(result) == abs_path

    def test_response_never_contains_secrets(self, client):
        r    = client.get("/api/auth/status")
        body = r.text
        assert "client_secret"  not in body
        assert "refresh_token"  not in body
        assert "access_token"   not in body
        assert "private_key"    not in body


# ── Videos list ───────────────────────────────────────────────────────────────

class TestVideosList:
    def test_empty_list(self, client):
        r = client.get("/api/videos")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_jobs(self, client, db):
        make_job(db, title="Video A")
        make_job(db, title="Video B")

        r     = client.get("/api/videos")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 2
        titles = {i["title"] for i in items}
        assert titles == {"Video A", "Video B"}

    def test_url_present_when_video_id_set(self, client, db):
        make_job(db, video_id="xyz999")

        r    = client.get("/api/videos")
        item = r.json()[0]
        assert item["url"] == "https://www.youtube.com/watch?v=xyz999"

    def test_no_credentials_in_video_list(self, client, db):
        make_job(db)

        body = client.get("/api/videos").text
        assert "client_secret"  not in body
        assert "refresh_token"  not in body


# ── Upload creation ────────────────────────────────────────────────────────────

class TestUploadCreation:
    def _fake_video_bytes(self) -> bytes:
        return b"\x00" * 1024   # 1 KB placeholder

    def _post_upload(self, client, extra_data: dict | None = None, mock_task=True):
        data = {
            "title":          "My Test Upload",
            "description":    "Test desc",
            "tags":           "tag1,tag2",
            "category_id":    "22",
            "privacy_status": "private",
        }
        if extra_data:
            data.update(extra_data)

        files = {"file": ("test.mp4", io.BytesIO(self._fake_video_bytes()), "video/mp4")}

        if mock_task:
            # Prevent real background upload from running
            with patch("backend.routers.uploads.BackgroundTasks.add_task"):
                return client.post("/api/uploads", data=data, files=files)
        return client.post("/api/uploads", data=data, files=files)

    def test_creates_job_and_returns_202(self, client):
        r = self._post_upload(client)
        assert r.status_code == 202
        body = r.json()
        assert "job_id"   in body
        assert body["status"]  == JobStatus.PENDING
        assert body["title"]   == "My Test Upload"
        assert body["progress"]== 0

    def test_missing_title_returns_422(self, client):
        files = {"file": ("t.mp4", io.BytesIO(b"\x00"), "video/mp4")}
        r = client.post("/api/uploads", data={"privacy_status": "private"}, files=files)
        assert r.status_code == 422

    def test_missing_file_returns_422(self, client):
        r = client.post("/api/uploads", data={"title": "No File", "privacy_status": "private"})
        assert r.status_code == 422

    def test_invalid_privacy_returns_422(self, client):
        r = self._post_upload(client, {"privacy_status": "top_secret"})
        assert r.status_code == 422

    def test_past_schedule_returns_422(self, client):
        r = self._post_upload(client, {"scheduled_at": "2020-01-01T00:00:00Z"})
        assert r.status_code == 422

    def test_future_schedule_accepted(self, client):
        r = self._post_upload(client, {"scheduled_at": future_iso(48)})
        assert r.status_code == 202
        body = r.json()
        assert body["scheduled_at"] is not None

    def test_no_credentials_in_response(self, client):
        r    = self._post_upload(client)
        body = r.text
        assert "client_secret"  not in body
        assert "refresh_token"  not in body
        assert "access_token"   not in body


# ── Upload status polling ──────────────────────────────────────────────────────

class TestUploadStatus:
    def test_returns_job_status(self, client, db):
        job = make_job(db, status=JobStatus.UPLOADING, progress=45)

        r = client.get(f"/api/uploads/{job.id}/status")
        assert r.status_code == 200
        body = r.json()
        assert body["status"]   == JobStatus.UPLOADING
        assert body["progress"] == 45

    def test_not_found_returns_404(self, client):
        r = client.get("/api/uploads/nonexistent-id/status")
        assert r.status_code == 404

    def test_completed_job_has_url(self, client, db):
        job = make_job(db, status=JobStatus.PUBLISHED, video_id="vid123", progress=100)

        r    = client.get(f"/api/uploads/{job.id}/status")
        body = r.json()
        assert body["url"] == "https://www.youtube.com/watch?v=vid123"
        assert body["video_id"] == "vid123"


# ── Delete job ─────────────────────────────────────────────────────────────────

class TestDeleteJob:
    def test_delete_returns_204(self, client, db):
        job = make_job(db)
        r = client.delete(f"/api/uploads/{job.id}")
        assert r.status_code == 204

    def test_deleted_job_not_found(self, client, db):
        job    = make_job(db)
        job_id = job.id
        client.delete(f"/api/uploads/{job_id}")
        r = client.get(f"/api/uploads/{job_id}/status")
        assert r.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/api/uploads/does-not-exist")
        assert r.status_code == 404


# ── Video update ──────────────────────────────────────────────────────────────

class TestVideoUpdate:
    def test_update_title(self, client):
        mock_response = {"id": "vid1", "snippet": {}, "status": {}}
        with patch("backend.services.youtube.update_video", return_value=mock_response):
            r = client.patch(
                "/api/videos/vid1",
                json={"title": "New Title"},
            )
        assert r.status_code == 200
        assert r.json()["video_id"] == "vid1"

    def test_reschedule(self, client):
        mock_response = {"id": "vid1", "snippet": {}, "status": {}}
        with patch("backend.services.youtube.update_video", return_value=mock_response):
            r = client.patch(
                "/api/videos/vid1",
                json={"scheduled_at": future_iso(72)},
            )
        assert r.status_code == 200

    def test_past_reschedule_rejected(self, client):
        r = client.patch(
            "/api/videos/vid1",
            json={"scheduled_at": "2020-01-01T00:00:00Z"},
        )
        assert r.status_code == 422

    def test_invalid_privacy_rejected(self, client):
        r = client.patch(
            "/api/videos/vid1",
            json={"privacy_status": "invisible"},
        )
        assert r.status_code == 422

    def test_clear_schedule(self, client):
        mock_response = {"id": "vid1", "snippet": {}, "status": {}}
        with patch("backend.services.youtube.update_video", return_value=mock_response):
            r = client.patch(
                "/api/videos/vid1",
                json={"clear_schedule": True},
            )
        assert r.status_code == 200

    def test_no_credentials_leaked(self, client):
        mock_response = {"id": "vid1", "snippet": {}, "status": {}}
        with patch("backend.services.youtube.update_video", return_value=mock_response):
            body = client.patch(
                "/api/videos/vid1",
                json={"title": "Safe"},
            ).text
        assert "client_secret"  not in body
        assert "refresh_token"  not in body


# ── Cancel schedule ────────────────────────────────────────────────────────────

class TestCancelSchedule:
    def test_cancel_schedule(self, client):
        with patch("backend.services.youtube.update_video", return_value={}):
            r = client.post("/api/videos/vid1/cancel-schedule")
        assert r.status_code == 200
        body = r.json()
        assert "cancelled" in body["message"].lower() or "private" in body["message"].lower()


# ── Scheduling validation (API level) ─────────────────────────────────────────

class TestSchedulingValidation:
    def test_explicit_utc_offset_accepted(self, client):
        """Datetime with +05:00 offset must be accepted for a future time."""
        future_pkt = (datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
            "%Y-%m-%dT%H:%M:%S+05:00"
        )
        files = {"file": ("t.mp4", io.BytesIO(b"\x00"), "video/mp4")}
        data  = {
            "title":        "PKT Test",
            "privacy_status": "private",
            "scheduled_at": future_pkt,
        }
        r = client.post("/api/uploads", data=data, files=files)
        assert r.status_code == 202

    def test_iana_timezone_accepted(self, client):
        """Datetime with IANA timezone helper must be accepted."""
        bare_future = (datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        files = {"file": ("t.mp4", io.BytesIO(b"\x00"), "video/mp4")}
        data  = {
            "title":        "TZ Test",
            "privacy_status": "private",
            "scheduled_at": bare_future,
            "timezone":     "Asia/Karachi",
        }
        r = client.post("/api/uploads", data=data, files=files)
        assert r.status_code == 202
