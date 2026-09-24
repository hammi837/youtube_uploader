"""
backend/migrations/phase_3i.py — Phase 3I audio profile columns.

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
    ("tts_voice",   "TEXT"),   # Optional per-job TTS voice (e.g. 'en-GB-SoniaNeural')
    ("music_style", "TEXT"),   # Optional per-job music style category (e.g. 'lofi')
]


def run(engine: Engine) -> None:
    """Add Phase 3I audio columns to content_queue_jobs if missing."""
    with engine.connect() as conn:
        raw = conn.connection
        cursor = raw.cursor()
        for col_name, col_type in _COLUMNS:
            try:
                cursor.execute(
                    f"ALTER TABLE content_queue_jobs ADD COLUMN {col_name} {col_type}"
                )
                logger.info("[phase_3i migration] Added column: %s", col_name)
            except Exception:
                # Column already exists — this is expected on subsequent startups
                logger.debug("[phase_3i migration] Column already exists (skipped): %s", col_name)
        raw.commit()
    logger.info("[phase_3i migration] Done.")
