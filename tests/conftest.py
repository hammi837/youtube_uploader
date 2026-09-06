"""
tests/conftest.py — Shared pytest configuration for the test suite.

Problem solved here:
    Both test_api.py and test_content_api.py set
    app.dependency_overrides[get_db] at module-import time, each pointing
    to a different in-memory engine.  Whichever file is imported second wins
    globally, causing the first file's DB-seeding fixtures to write to a
    different engine than the one the app is actually using.

Solution:
    A single, shared StaticPool engine is created here (in conftest.py,
    which is imported before any test module).  Both test modules import and
    use THIS engine via the helpers exported below.  The app override is set
    once, here, before any test runs.

    Each test still gets full table isolation via the autouse reset_db
    fixture defined in each test file.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ── Single shared in-memory engine ────────────────────────────────────────────
SHARED_TEST_DATABASE_URL = "sqlite://"

shared_engine = create_engine(
    SHARED_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SharedTestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=shared_engine
)

# ── Register the DB override on the app ONCE, before any test module loads ────
# Import order: conftest.py is always loaded first by pytest.
from backend.db import Base, get_db  # noqa: E402
from backend.main import app  # noqa: E402

# Ensure ALL tables (upload_jobs + content_projects etc.) exist
Base.metadata.create_all(bind=shared_engine)


def _override_get_db():
    db = SharedTestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
