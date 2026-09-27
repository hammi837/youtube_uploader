"""
tests/test_analytics.py — Phase 3K analytics tests.

Tests for analytics service, endpoints, and error handling.
Tests use clearly identifiable TEST_ANALYTICS_ records that can be cleaned up.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Simple tests that don't require database isolation ───────────────────────


def test_summary_metrics_invalid_date_range():
    """Test summary metrics with invalid date range."""
    from backend.services.analytics import compute_summary_metrics

    with pytest.raises(ValueError, match="Invalid days parameter"):
        compute_summary_metrics("invalid")


def test_performance_metrics_invalid_date_range():
    """Test performance metrics with invalid date range."""
    from backend.services.analytics import compute_performance_metrics

    with pytest.raises(ValueError, match="Invalid days parameter"):
        compute_performance_metrics("invalid")


def test_query_jobs_invalid_date_range():
    """Test job query with invalid date range."""
    from backend.services.analytics import query_jobs

    with pytest.raises(ValueError, match="Invalid days parameter"):
        query_jobs("invalid")


def test_query_jobs_page_size_limit():
    """Test that page_size is limited to max 200."""
    from backend.services.analytics import query_jobs

    # Try to request page_size larger than max - should be capped, not raise error
    result = query_jobs("7", page=1, page_size=300)
    assert result.page_size == 200  # Should be capped to max


# ── Resource usage tests (mocked) ─────────────────────────────────────────────


@patch("backend.services.analytics.check_disk_space")
def test_resource_usage_current_snapshot(mock_check_disk):
    """Test resource usage returns current snapshot."""
    mock_check_disk.return_value = (True, 50.0)

    from backend.services.analytics import compute_current_resource_usage

    result = compute_current_resource_usage()
    assert result.disk_usage_gb >= 0
    assert result.free_disk_gb == 50.0
    assert result.temp_size_gb >= 0
    assert result.audio_size_gb >= 0
    assert result.video_size_gb >= 0
    assert result.ai_images_size_gb >= 0
    assert "snapshot" in result.note.lower()
    assert "not historical" in result.note.lower()


# ── Endpoint integration tests ───────────────────────────────────────────────────


def test_analytics_endpoints():
    """Test that analytics endpoints are accessible."""
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)

    # Test summary endpoint
    response = client.get("/api/analytics/summary?days=7")
    assert response.status_code == 200
    data = response.json()
    assert "date_range" in data
    assert "total_jobs" in data
    assert "data_availability_note" in data

    # Test performance endpoint
    response = client.get("/api/analytics/performance?days=7")
    assert response.status_code == 200
    data = response.json()
    assert "date_range" in data
    assert "jobs_completed" in data

    # Test errors endpoint
    response = client.get("/api/analytics/errors?days=7")
    assert response.status_code == 200
    data = response.json()
    assert "date_range" in data
    assert "total_errors" in data

    # Test resources endpoint
    response = client.get("/api/analytics/resources")
    assert response.status_code == 200
    data = response.json()
    assert "disk_usage_gb" in data
    assert "note" in data

    # Test jobs endpoint
    response = client.get("/api/analytics/jobs?days=7")
    assert response.status_code == 200
    data = response.json()
    assert "total_count" in data
    assert "jobs" in data


def test_analytics_endpoints_invalid_days():
    """Test that invalid days parameter returns 400."""
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)

    response = client.get("/api/analytics/summary?days=invalid")
    assert response.status_code == 400


def test_analytics_endpoints_db_failure():
    """Test that database failure returns 503."""
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)

    # Mock the router function to raise an exception
    with patch("backend.routers.analytics.compute_summary_metrics", side_effect=Exception("DB failure")):
        response = client.get("/api/analytics/summary?days=7")
        assert response.status_code == 503
        data = response.json()
        assert "temporarily unavailable" in data["detail"].lower()


def test_analytics_endpoints_date_ranges():
    """Test that all date ranges work."""
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)

    for days in ["7", "30", "all"]:
        response = client.get(f"/api/analytics/summary?days={days}")
        assert response.status_code == 200
        data = response.json()
        assert data["date_range"] == days


def test_analytics_endpoints_response_models():
    """Test that response models have required fields."""
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)

    # Summary
    response = client.get("/api/analytics/summary?days=7")
    assert response.status_code == 200
    data = response.json()
    required_fields = ["date_range", "total_jobs", "completed_jobs", "failed_jobs",
                      "success_rate", "avg_total_duration_seconds", "uploads_today",
                      "current_disk_usage_gb", "data_availability_note"]
    for field in required_fields:
        assert field in data

    # Performance
    response = client.get("/api/analytics/performance?days=7")
    assert response.status_code == 200
    data = response.json()
    required_fields = ["date_range", "jobs_completed", "jobs_with_retries", "retry_rate",
                      "avg_total_duration_seconds", "p50_duration_seconds",
                      "p90_duration_seconds", "p95_duration_seconds", "data_availability_note"]
    for field in required_fields:
        assert field in data

    # Errors
    response = client.get("/api/analytics/errors?days=7")
    assert response.status_code == 200
    data = response.json()
    required_fields = ["date_range", "total_errors", "transient_errors", "permanent_errors",
                      "unknown_errors", "error_by_type", "error_by_stage",
                      "top_error_messages", "data_availability_note"]
    for field in required_fields:
        assert field in data

    # Resources
    response = client.get("/api/analytics/resources")
    assert response.status_code == 200
    data = response.json()
    required_fields = ["disk_usage_gb", "free_disk_gb", "temp_size_gb", "audio_size_gb",
                      "video_size_gb", "ai_images_size_gb", "note"]
    for field in required_fields:
        assert field in data

    # Jobs
    response = client.get("/api/analytics/jobs?days=7")
    assert response.status_code == 200
    data = response.json()
    required_fields = ["date_range", "total_count", "page", "page_size", "jobs",
                      "data_availability_note"]
    for field in required_fields:
        assert field in data


def test_error_classification_determinism():
    """Test that error classification is deterministic and mutually exclusive."""
    from backend.services.analytics import _classify_error

    # Test transient error types
    assert _classify_error("TimeoutError", None) == "transient"
    assert _classify_error("ConnectionError", None) == "transient"
    assert _classify_error("HTTPError", None) == "transient"

    # Test permanent error types
    assert _classify_error("FileNotFoundError", None) == "permanent"
    assert _classify_error("ValueError", None) == "permanent"
    assert _classify_error("ValidationError", None) == "permanent"
    assert _classify_error("AuthenticationError", None) == "permanent"
    assert _classify_error("PermissionError", None) == "permanent"

    # Test transient keywords in error message
    assert _classify_error(None, "Connection refused") == "transient"
    assert _classify_error(None, "Network timeout") == "transient"
    assert _classify_error(None, "SSL certificate error") == "transient"

    # Test NULL/unclassifiable error (should be unknown, not permanent)
    assert _classify_error(None, None) == "unknown"
    assert _classify_error("UnknownError", None) == "unknown"
    assert _classify_error(None, "Some random error") == "unknown"

    # Test that error type takes precedence over message
    assert _classify_error("TimeoutError", "This is a ValueError") == "transient"
    assert _classify_error("ValueError", "Connection refused") == "permanent"

    print("Error classification determinism test passed")


def test_error_classification_determinism():
    """Test that error classification is deterministic and mutually exclusive."""
    from backend.services.analytics import _classify_error

    # Test transient error types
    assert _classify_error("TimeoutError", None) == "transient"
    assert _classify_error("ConnectionError", None) == "transient"
    assert _classify_error("HTTPError", None) == "transient"

    # Test permanent error types
    assert _classify_error("FileNotFoundError", None) == "permanent"
    assert _classify_error("ValueError", None) == "permanent"
    assert _classify_error("ValidationError", None) == "permanent"
    assert _classify_error("AuthenticationError", None) == "permanent"
    assert _classify_error("PermissionError", None) == "permanent"

    # Test transient keywords in error message
    assert _classify_error(None, "Connection refused") == "transient"
    assert _classify_error(None, "Network timeout") == "transient"
    assert _classify_error(None, "SSL certificate error") == "transient"

    # Test NULL/unclassifiable error (should be unknown, not permanent)
    assert _classify_error(None, None) == "unknown"
    assert _classify_error("UnknownError", None) == "unknown"
    assert _classify_error(None, "Some random error") == "unknown"

    # Test that error type takes precedence over message
    assert _classify_error("TimeoutError", "This is a ValueError") == "transient"
    assert _classify_error("ValueError", "Connection refused") == "permanent"

    print("Error classification determinism test passed")


def test_ai_image_resource_path():
    """Test that resource usage uses the correct AI image directory (generated_images)."""
    import os
    from pathlib import Path
    from backend.services.analytics import compute_current_resource_usage
    from backend.services.visual.ai_image_backend import get_ai_image_output_dir

    # Get the actual AI image output directory
    ai_image_dir = get_ai_image_output_dir()
    print(f"AI image directory: {ai_image_dir}")

    # Ensure directory exists
    ai_image_dir.mkdir(parents=True, exist_ok=True)

    # Create a test file of known size (1 MB)
    test_file = ai_image_dir / "test_analytics_ai_image.jpg"
    test_size_bytes = 1024 * 1024  # 1 MB
    test_file.write_bytes(b"x" * test_size_bytes)

    try:
        # Compute resource usage
        resources = compute_current_resource_usage()
        print(f"AI images size from resources: {resources.ai_images_size_gb} GB")

        # The AI images size should be at least 1 MB
        ai_images_size_bytes = resources.ai_images_size_gb * 1024 * 1024 * 1024
        assert ai_images_size_bytes >= test_size_bytes, f"Expected at least {test_size_bytes} bytes, got {ai_images_size_bytes}"

        print("AI image resource path test passed")

    finally:
        # Cleanup test file
        if test_file.exists():
            test_file.unlink()
            print(f"Cleaned up test file: {test_file}")


def test_retry_rate_date_range_filtering():
    import os
    import tempfile
    from pathlib import Path
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.services.analytics import compute_performance_metrics
    from datetime import datetime, timedelta, timezone
    from backend.db import Base
    from backend.queue_models import ContentQueueJob, QueueStatus

    # Import all models to resolve SQLAlchemy relationships
    import backend.content_models  # noqa: F401
    import backend.tts_models  # noqa: F401
    import backend.video_generation_models  # noqa: F401

    # Create isolated test database
    test_db_path = Path(tempfile.gettempdir()) / f"test_retry_date_{os.getpid()}.db"
    engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocalTest = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocalTest()
    try:
        now = datetime.now(timezone.utc)

        # Create 5 jobs with retries in the last 7 days (use very recent dates)
        for i in range(5):
            created = now - timedelta(hours=i * 12)  # Every 12 hours
            started = created + timedelta(minutes=1)
            completed = started + timedelta(minutes=3)
            job = ContentQueueJob(
                topic=f"RETRY_TEST_recent_{i}",
                language="en",
                tone="engaging",
                target_duration_seconds=180,
                scene_count=10,
                status=QueueStatus.COMPLETED,
                retry_count=2,  # Has retries
                started_at=started,
                completed_at=completed,
                created_at=created,
            )
            db.add(job)

        # Create 5 jobs with retries outside 7 days (10-15 days ago)
        for i in range(5):
            created = now - timedelta(days=10 + i)
            started = created + timedelta(minutes=1)
            completed = started + timedelta(minutes=3)
            job = ContentQueueJob(
                topic=f"RETRY_TEST_old_{i}",
                language="en",
                tone="engaging",
                target_duration_seconds=180,
                scene_count=10,
                status=QueueStatus.COMPLETED,
                retry_count=2,  # Has retries
                started_at=started,
                completed_at=completed,
                created_at=created,
            )
            db.add(job)

        # Create 5 jobs without retries in the last 7 days
        for i in range(5):
            created = now - timedelta(hours=i * 12 + 5)  # Every 12 hours, offset
            started = created + timedelta(minutes=1)
            completed = started + timedelta(minutes=3)
            job = ContentQueueJob(
                topic=f"RETRY_test_no_retry_{i}",
                language="en",
                tone="engaging",
                target_duration_seconds=180,
                scene_count=10,
                status=QueueStatus.COMPLETED,
                retry_count=0,  # No retries
                started_at=started,
                completed_at=completed,
                created_at=created,
            )
            db.add(job)

        db.commit()
        db.close()

        # Mock SessionLocal to use our test database
        import backend.services.analytics as analytics_module
        original_session_local = analytics_module.SessionLocal
        analytics_module.SessionLocal = SessionLocalTest

        try:
            # Test 7-day range should only include recent jobs
            perf_7d = compute_performance_metrics("7")
            # 5 recent with retries + 5 recent without retries = 10 total jobs
            assert perf_7d.jobs_completed == 10, f"Expected 10 jobs in 7-day range, got {perf_7d.jobs_completed}"
            assert perf_7d.jobs_with_retries == 5, f"Expected 5 with retries in 7-day range, got {perf_7d.jobs_with_retries}"
            assert perf_7d.retry_rate == 0.5, f"Expected 50% retry rate in 7-day range, got {perf_7d.retry_rate}"

            # Test 30-day range should include all jobs
            perf_30d = compute_performance_metrics("30")
            # 5 recent with retries + 5 old with retries + 5 recent without retries = 15 total jobs
            assert perf_30d.jobs_completed == 15, f"Expected 15 jobs in 30-day range, got {perf_30d.jobs_completed}"
            assert perf_30d.jobs_with_retries == 10, f"Expected 10 with retries in 30-day range, got {perf_30d.jobs_with_retries}"
            assert perf_30d.retry_rate == 10/15, f"Expected {10/15} retry rate in 30-day range, got {perf_30d.retry_rate}"

            print("Retry rate date range filtering test passed")

        finally:
            # Restore original SessionLocal
            analytics_module.SessionLocal = original_session_local

    finally:
        # Cleanup
        db = SessionLocalTest()
        db.query(ContentQueueJob).filter(ContentQueueJob.topic.like("RETRY_TEST%")).delete()
        db.commit()
        db.close()
        engine.dispose()
        if test_db_path.exists():
            test_db_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
