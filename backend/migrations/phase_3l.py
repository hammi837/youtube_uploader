"""
backend/migrations/phase_3l.py — Phase 3L content planning tables and columns.

Safe, idempotent SQLite migration.
Runs at startup via main.py lifespan.
Does NOT modify existing data or drop anything.

Migration steps:
1. Create content_plans table
2. Add plan_id column to content_projects
3. Add 'planned' to ContentStatus (handled in code, not DB)
"""

from __future__ import annotations

import logging
from sqlalchemy.engine import Engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


def run(engine: Engine) -> None:
    """Add Phase 3L content planning tables and columns if missing."""
    with engine.connect() as conn:
        raw = conn.connection
        cursor = raw.cursor()

        # ── Step 1: Create content_plans table ──────────────────────────────────
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS content_plans (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    schedule_start TIMESTAMP,
                    schedule_interval_minutes INTEGER,
                    schedule_timezone TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            logger.info("[phase_3l migration] Created table: content_plans")
        except Exception as exc:
            logger.error("[phase_3l migration] Failed to create content_plans table: %s", exc)
            raise

        # ── Step 2: Add plan_id column to content_projects ───────────────────────
        try:
            cursor.execute(
                "ALTER TABLE content_projects ADD COLUMN plan_id TEXT"
            )
            logger.info("[phase_3l migration] Added column: content_projects.plan_id")
        except Exception:
            # Column already exists — this is expected on subsequent startups
            logger.debug("[phase_3l migration] Column already exists (skipped): content_projects.plan_id")

        # ── Step 3: Add foreign key constraint (SQLite requires separate step) ───────
        try:
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS fk_content_projects_plan_id
                BEFORE DELETE ON content_plans
                FOR EACH ROW
                BEGIN
                    UPDATE content_projects SET plan_id = NULL WHERE plan_id = OLD.id;
                END
            """)
            logger.info("[phase_3l migration] Created trigger: fk_content_projects_plan_id")
        except Exception as exc:
            logger.debug("[phase_3l migration] Trigger already exists or failed (skipped): %s", exc)

        raw.commit()
    logger.info("[phase_3l migration] Done.")
