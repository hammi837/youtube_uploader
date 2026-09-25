"""
backend/migrations/phase_3j.py — Phase 3J thumbnail style column.

Safe, idempotent SQLite migration.
Runs at startup via main.py lifespan.
Does NOT modify existing data or drop anything.
"""

from __future__ import annotations

import logging
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Columns to add to content_queue_jobs
_COLUMNS = [
    ("thumbnail_style", "TEXT"),   # Optional per-job thumbnail style (e.g. 'scene_frame')
]


def run(engine: Engine) -> None:
    """Add Phase 3J thumbnail_style column to content_queue_jobs if missing."""
    with engine.connect() as conn:
        raw = conn.connection
        cursor = raw.cursor()
        for col_name, col_type in _COLUMNS:
            try:
                cursor.execute(
                    f"ALTER TABLE content_queue_jobs ADD COLUMN {col_name} {col_type}"
                )
                logger.info("[phase_3j migration] Added column: %s", col_name)
            except Exception:
                # Column already exists — this is expected on subsequent startups
                logger.debug("[phase_3j migration] Column already exists (skipped): %s", col_name)
        raw.commit()
    logger.info("[phase_3j migration] Done.")
