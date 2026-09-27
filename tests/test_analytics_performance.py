"""
tests/test_analytics_performance.py — Phase 3K 100+ job performance test.

Creates an isolated test database with 100+ synthetic ContentQueueJob records,
runs analytics queries, measures performance, and cleans up.
"""

import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db import Base
from backend.queue_models import ContentQueueJob, QueueStatus


def get_test_db_path():
    """Get a unique test database path."""
    temp_dir = Path(tempfile.gettempdir())
    return temp_dir / f"test_analytics_perf_{os.getpid()}.db"


def create_test_job(
    session,
    topic="Test Topic",
    status=QueueStatus.QUEUED,
    retry_count=0,
    started_at=None,
    completed_at=None,
    failed_at=None,
    last_error_type=None,
    error_message=None,
    production_stage=None,
):
    """Create a test queue job."""
    job = ContentQueueJob(
        topic=topic,
        language="en",
        tone="engaging",
        target_duration_seconds=180,
        scene_count=10,
        status=status,
        retry_count=retry_count,
        started_at=started_at,
        completed_at=completed_at,
        failed_at=failed_at,
        last_error_type=last_error_type,
        error_message=error_message,
        production_stage=production_stage,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_100_plus_job_performance():
    """
    Test analytics with 100+ job records to verify acceptable performance.
    Target: <5 seconds for all queries.
    """
    test_db_path = get_test_db_path()

    # Create isolated test database
    engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=5)
    completed = now - timedelta(minutes=2)

    # Create 105 synthetic jobs
    job_ids = []
    for i in range(105):
        status = QueueStatus.COMPLETED if i % 3 != 0 else QueueStatus.FAILED
        retry_count = 1 if i % 5 == 0 else 0
        job = create_test_job(
            db,
            topic=f"PERF_TEST_{i}",
            status=status,
            retry_count=retry_count,
            started_at=started,
            completed_at=completed if i % 3 != 0 else None,
            failed_at=completed if i % 3 == 0 else None,
            last_error_type="TimeoutError" if i % 7 == 0 else None,
            error_message="Connection timeout" if i % 7 == 0 else None,
            production_stage="uploading" if i % 7 == 0 else None,
        )
        job_ids.append(job.id)

    db.close()

    print(f"\n=== Performance Test with {len(job_ids)} jobs ===")

    # Mock SessionLocal to use our test database
    import backend.services.analytics as analytics_module
    original_session_local = analytics_module.SessionLocal
    analytics_module.SessionLocal = SessionLocal

    try:
        from backend.services.analytics import (
            aggregate_errors,
            compute_performance_metrics,
            compute_summary_metrics,
            query_jobs,
        )

        # Test summary metrics performance
        start = time.time()
        summary = compute_summary_metrics("all")
        summary_time = time.time() - start
        print(f"Summary metrics: {summary_time:.3f}s")
        assert summary.total_jobs == 105, f"Expected 105 jobs, got {summary.total_jobs}"
        assert summary_time < 5.0, f"Summary query took {summary_time:.3f}s (target: <5s)"

        # Test performance metrics
        start = time.time()
        performance = compute_performance_metrics("all")
        perf_time = time.time() - start
        print(f"Performance metrics: {perf_time:.3f}s")
        assert performance.jobs_completed == 70, f"Expected 70 completed, got {performance.jobs_completed}"
        assert performance.jobs_with_retries == 21, f"Expected 21 with retries, got {performance.jobs_with_retries}"
        assert perf_time < 5.0, f"Performance query took {perf_time:.3f}s (target: <5s)"

        # Test error aggregation
        start = time.time()
        errors = aggregate_errors("all")
        errors_time = time.time() - start
        print(f"Error aggregation: {errors_time:.3f}s")
        assert errors.total_errors == 35, f"Expected 35 errors, got {errors.total_errors}"
        assert errors_time < 5.0, f"Error aggregation took {errors_time:.3f}s (target: <5s)"

        # Test job query with pagination
        start = time.time()
        jobs = query_jobs("all", page=1, page_size=50)
        jobs_time = time.time() - start
        print(f"Job query (page 1): {jobs_time:.3f}s")
        assert jobs.total_count == 105, f"Expected 105 total, got {jobs.total_count}"
        assert len(jobs.jobs) == 50, f"Expected 50 on page 1, got {len(jobs.jobs)}"
        assert jobs_time < 5.0, f"Job query took {jobs_time:.3f}s (target: <5s)"

        # Test page 2
        start = time.time()
        jobs_page2 = query_jobs("all", page=2, page_size=50)
        jobs_page2_time = time.time() - start
        print(f"Job query (page 2): {jobs_page2_time:.3f}s")
        assert len(jobs_page2.jobs) == 50, f"Expected 50 on page 2, got {len(jobs_page2.jobs)}"
        assert jobs_page2_time < 5.0, f"Job query page 2 took {jobs_page2_time:.3f}s (target: <5s)"

        # Test page 3
        start = time.time()
        jobs_page3 = query_jobs("all", page=3, page_size=50)
        jobs_page3_time = time.time() - start
        print(f"Job query (page 3): {jobs_page3_time:.3f}s")
        assert len(jobs_page3.jobs) == 5, f"Expected 5 on page 3, got {len(jobs_page3.jobs)}"
        assert jobs_page3_time < 5.0, f"Job query page 3 took {jobs_page3_time:.3f}s (target: <5s)"

        total_time = summary_time + perf_time + errors_time + jobs_time + jobs_page2_time + jobs_page3_time
        print(f"\nTotal query time: {total_time:.3f}s")
        print(f"Target: <5s per query (all passed)")

    finally:
        # Restore original SessionLocal
        analytics_module.SessionLocal = original_session_local

        # Cleanup: delete all test jobs
        db = SessionLocal()
        for job_id in job_ids:
            db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).delete()
        db.commit()
        db.close()

        print(f"[OK] Cleanup: Deleted {len(job_ids)} test jobs")

        # Dispose engine to release file handle
        engine.dispose()

        if test_db_path.exists():
            test_db_path.unlink()
            print(f"[OK] Cleanup: Deleted test database")


if __name__ == "__main__":
    test_100_plus_job_performance()
