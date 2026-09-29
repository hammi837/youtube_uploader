"""
backend/services/plan_service.py — Phase 3L content planning service.

Business logic for content plans:
- Create/update/delete plans
- Add/remove plan items (ContentProjects)
- Generate scripts for plan items
- Approve/reject plans
- Queue approved projects
- Duplicate protection
- Plan completion state tracking

All functions use their own SessionLocal (safe to call from background threads).
No credentials, tokens, or secrets are ever logged or returned.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Plan CRUD ────────────────────────────────────────────────────────────────

def create_plan(
    name: str,
    description: Optional[str],
    schedule_start: Optional[str],
    schedule_interval_minutes: Optional[int],
    schedule_timezone: Optional[str],
) -> str:
    """
    Create a new content plan.

    Returns the plan ID.
    """
    from backend.db import SessionLocal
    from backend.plan_models import ContentPlan, PlanStatus

    db = SessionLocal()
    try:
        # Parse schedule if provided
        parsed_schedule_start = None
        if schedule_start:
            from backend.services.scheduler import parse_schedule_time_with_zone
            if schedule_timezone:
                parsed_schedule_start = parse_schedule_time_with_zone(schedule_start, schedule_timezone)
            else:
                from backend.services.scheduler import parse_schedule_time
                parsed_schedule_start = parse_schedule_time(schedule_start)

        plan = ContentPlan(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            status=PlanStatus.DRAFT,
            schedule_start=parsed_schedule_start,
            schedule_interval_minutes=schedule_interval_minutes,
            schedule_timezone=schedule_timezone,
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        logger.info("Created plan: id=%s name='%s'", plan.id, plan.name)
        return plan.id
    finally:
        db.close()


def update_plan(
    plan_id: str,
    name: Optional[str],
    description: Optional[str],
    status: Optional[str],
    schedule_start: Optional[str],
    schedule_interval_minutes: Optional[int],
    schedule_timezone: Optional[str],
) -> bool:
    """
    Update a content plan.

    Returns True if updated, False if not found.
    """
    from backend.db import SessionLocal
    from backend.plan_models import ContentPlan, PlanStatus

    db = SessionLocal()
    try:
        plan = db.query(ContentPlan).filter(ContentPlan.id == plan_id).first()
        if not plan:
            return False

        # Validate status transition
        if status and status != plan.status:
            valid_transitions = PlanStatus.TRANSITIONS.get(plan.status, set())
            if status not in valid_transitions:
                raise ValueError(
                    f"Invalid status transition: {plan.status} → {status}. "
                    f"Valid transitions: {valid_transitions}"
                )
            plan.status = status

        if name is not None:
            plan.name = name
        if description is not None:
            plan.description = description
        if schedule_start is not None:
            from backend.services.scheduler import parse_schedule_time_with_zone, parse_schedule_time
            if schedule_timezone:
                plan.schedule_start = parse_schedule_time_with_zone(schedule_start, schedule_timezone)
            else:
                plan.schedule_start = parse_schedule_time(schedule_start)
        if schedule_interval_minutes is not None:
            plan.schedule_interval_minutes = schedule_interval_minutes
        if schedule_timezone is not None:
            plan.schedule_timezone = schedule_timezone

        db.commit()
        logger.info("Updated plan: id=%s", plan_id)
        return True
    finally:
        db.close()


def delete_plan(plan_id: str) -> bool:
    """
    Delete a content plan.

    Returns True if deleted, False if not found or deletion blocked.
    """
    from backend.db import SessionLocal
    from backend.plan_models import ContentPlan, PlanStatus
    from backend.queue_models import ContentQueueJob, QueueStatus

    db = SessionLocal()
    try:
        plan = db.query(ContentPlan).filter(ContentPlan.id == plan_id).first()
        if not plan:
            return False

        # Prevent deletion if plan is approved or completed (has production data)
        if plan.status in {PlanStatus.APPROVED, PlanStatus.COMPLETED}:
            # Check if any projects in this plan have queue jobs
            from backend.content_models import ContentProject
            projects = db.query(ContentProject).filter(ContentProject.plan_id == plan_id).all()
            for project in projects:
                queue_job = db.query(ContentQueueJob).filter(
                    ContentQueueJob.content_project_id == project.id
                ).first()
                if queue_job and queue_job.status in QueueStatus.ACTIVE:
                    raise ValueError(
                        f"Cannot delete plan {plan_id}: has active queue jobs. "
                        "Cancel jobs first or delete completed jobs."
                    )

        db.delete(plan)
        db.commit()
        logger.info("Deleted plan: id=%s", plan_id)
        return True
    finally:
        db.close()


def get_plan(plan_id: str) -> Optional[dict]:
    """
    Get a content plan by ID.

    Returns plan dict or None if not found.
    """
    from backend.db import SessionLocal
    from backend.plan_models import ContentPlan

    db = SessionLocal()
    try:
        plan = db.query(ContentPlan).filter(ContentPlan.id == plan_id).first()
        if not plan:
            return None

        return {
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "status": plan.status,
            "schedule_start": plan.schedule_start.isoformat() if plan.schedule_start else None,
            "schedule_interval_minutes": plan.schedule_interval_minutes,
            "schedule_timezone": plan.schedule_timezone,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
            "item_count": len(plan.projects) if plan.projects else 0,
        }
    finally:
        db.close()


def list_plans() -> list[dict]:
    """
    List all content plans.

    Returns list of plan dicts.
    """
    from backend.db import SessionLocal
    from backend.plan_models import ContentPlan

    db = SessionLocal()
    try:
        plans = db.query(ContentPlan).order_by(ContentPlan.created_at.desc()).all()
        return [
            {
                "id": plan.id,
                "name": plan.name,
                "description": plan.description,
                "status": plan.status,
                "schedule_start": plan.schedule_start.isoformat() if plan.schedule_start else None,
                "schedule_interval_minutes": plan.schedule_interval_minutes,
                "schedule_timezone": plan.schedule_timezone,
                "created_at": plan.created_at.isoformat() if plan.created_at else None,
                "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
                "item_count": len(plan.projects) if plan.projects else 0,
            }
            for plan in plans
        ]
    finally:
        db.close()


# ── Plan Items (ContentProjects) ───────────────────────────────────────────────

def add_plan_item(
    plan_id: str,
    topic: str,
    language: str,
    tone: str,
    target_duration_seconds: int,
    scene_count: int,
) -> str:
    """
    Add a ContentProject to a plan.

    Returns the project ID.
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject, ContentStatus
    from backend.plan_models import ContentPlan

    db = SessionLocal()
    try:
        # Verify plan exists
        plan = db.query(ContentPlan).filter(ContentPlan.id == plan_id).first()
        if not plan:
            raise ValueError(f"Plan not found: {plan_id}")

        # Check for duplicate topics within the same plan
        existing = db.query(ContentProject).filter(
            ContentProject.plan_id == plan_id,
            ContentProject.topic.ilike(topic)
        ).first()
        if existing:
            raise ValueError(f"Duplicate topic in plan: '{topic}'")

        # Create ContentProject with "planned" status
        project = ContentProject(
            id=str(uuid.uuid4()),
            topic=topic,
            language=language,
            tone=tone,
            target_duration_seconds=target_duration_seconds,
            scene_count=scene_count,
            status=ContentStatus.PLANNED,
            plan_id=plan_id,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        logger.info("Added plan item: plan_id=%s project_id=%s topic='%s'", plan_id, project.id, topic)
        return project.id
    finally:
        db.close()


def update_plan_item(
    item_id: str,
    topic: Optional[str],
    language: Optional[str],
    tone: Optional[str],
    target_duration_seconds: Optional[int],
    scene_count: Optional[int],
) -> bool:
    """
    Update a plan item (ContentProject).

    Returns True if updated, False if not found.
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject

    db = SessionLocal()
    try:
        project = db.query(ContentProject).filter(ContentProject.id == item_id).first()
        if not project:
            return False

        # Only allow updates if project is in "planned" status
        if project.status != "planned":
            raise ValueError(f"Cannot update item with status '{project.status}'. Only 'planned' items can be edited.")

        if topic is not None:
            # Check for duplicate topics within the same plan
            if project.plan_id:
                existing = db.query(ContentProject).filter(
                    ContentProject.plan_id == project.plan_id,
                    ContentProject.topic.ilike(topic),
                    ContentProject.id != item_id
                ).first()
                if existing:
                    raise ValueError(f"Duplicate topic in plan: '{topic}'")
            project.topic = topic
        if language is not None:
            project.language = language
        if tone is not None:
            project.tone = tone
        if target_duration_seconds is not None:
            project.target_duration_seconds = target_duration_seconds
        if scene_count is not None:
            project.scene_count = scene_count

        db.commit()
        logger.info("Updated plan item: project_id=%s", item_id)
        return True
    finally:
        db.close()


def remove_plan_item(item_id: str) -> bool:
    """
    Remove a plan item (ContentProject).

    Returns True if removed, False if not found or blocked.
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject
    from backend.queue_models import ContentQueueJob

    db = SessionLocal()
    try:
        project = db.query(ContentProject).filter(ContentProject.id == item_id).first()
        if not project:
            return False

        # Prevent deletion if project has a queue job
        queue_job = db.query(ContentQueueJob).filter(
            ContentQueueJob.content_project_id == item_id
        ).first()
        if queue_job:
            raise ValueError(
                f"Cannot remove item {item_id}: has associated queue job. "
                "Cancel the queue job first."
            )

        db.delete(project)
        db.commit()
        logger.info("Removed plan item: project_id=%s", item_id)
        return True
    finally:
        db.close()


def list_plan_items(plan_id: str) -> list[dict]:
    """
    List all items (ContentProjects) in a plan.

    Returns list of item dicts.
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject

    db = SessionLocal()
    try:
        projects = db.query(ContentProject).filter(
            ContentProject.plan_id == plan_id
        ).order_by(ContentProject.created_at.asc()).all()

        return [
            {
                "id": p.id,
                "plan_id": p.plan_id,
                "topic": p.topic,
                "language": p.language,
                "tone": p.tone,
                "target_duration_seconds": p.target_duration_seconds,
                "scene_count": p.scene_count,
                "status": p.status,
                "error_message": p.error_message,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                "has_script": p.script is not None,
                "script_title": p.script.title if p.script else None,
            }
            for p in projects
        ]
    finally:
        db.close()


# ── Script Generation ─────────────────────────────────────────────────────────

def generate_plan_scripts(plan_id: str) -> dict:
    """
    Generate scripts for all planned items in a plan.

    Processes items sequentially, isolating failures per item.

    Returns dict with:
    - total: total items
    - succeeded: count of successful generations
    - failed: count of failed generations
    - errors: list of error messages
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject, ContentStatus
    from backend.plan_models import ContentPlan

    db = SessionLocal()
    try:
        plan = db.query(ContentPlan).filter(ContentPlan.id == plan_id).first()
        if not plan:
            raise ValueError(f"Plan not found: {plan_id}")

        projects = db.query(ContentProject).filter(
            ContentProject.plan_id == plan_id,
            ContentProject.status == ContentStatus.PLANNED
        ).order_by(ContentProject.created_at.asc()).all()

        if not projects:
            return {"total": 0, "succeeded": 0, "failed": 0, "errors": []}

        total = len(projects)
        succeeded = 0
        failed = 0
        errors = []

        for project in projects:
            try:
                # Generate script using existing service
                from backend.content_models import ScriptRequest
                from backend.services.script_generator import generate_script

                request = ScriptRequest(
                    topic=project.topic,
                    language=project.language,
                    tone=project.tone,
                    target_duration_seconds=project.target_duration_seconds,
                    scene_count=project.scene_count,
                )

                research, script = generate_script(request)

                # Persist script
                import json
                from backend.content_models import GeneratedScriptRecord, ResearchSourceRecord

                # Persist research sources
                for src in research.sources:
                    source_record = ResearchSourceRecord(
                        id=str(uuid.uuid4()),
                        content_project_id=project.id,
                        title=src.title,
                        url=src.url,
                        snippet=src.snippet,
                        source_data=json.dumps(src.key_points),
                    )
                    db.add(source_record)

                # Persist generated script
                script_record = GeneratedScriptRecord(
                    id=str(uuid.uuid4()),
                    content_project_id=project.id,
                    title=script.title,
                    description=script.description,
                    hook=script.hook,
                    tags_json=json.dumps(script.tags),
                    scenes_json=json.dumps([s.model_dump() for s in script.scenes]),
                    estimated_duration_seconds=script.estimated_duration_seconds,
                )
                db.add(script_record)

                # Update project status to "pending" (ready for review/queue)
                project.status = ContentStatus.PENDING
                db.commit()

                succeeded += 1
                logger.info("Generated script for project: id=%s topic='%s'", project.id, project.topic)

            except Exception as exc:
                failed += 1
                error_msg = f"Failed to generate script for '{project.topic}': {str(exc)[:200]}"
                errors.append(error_msg)
                project.status = ContentStatus.FAILED
                project.error_message = error_msg
                db.commit()
                logger.error("Script generation failed for project %s: %s", project.id, exc)

        logger.info(
            "Plan script generation complete: plan_id=%s total=%d succeeded=%d failed=%d",
            plan_id, total, succeeded, failed
        )

        return {
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "errors": errors,
        }
    finally:
        db.close()


# ── Plan Approval ────────────────────────────────────────────────────────────

def approve_plan(
    plan_id: str,
    schedule_start: Optional[str],
    schedule_interval_minutes: Optional[int],
    schedule_timezone: Optional[str],
) -> dict:
    """
    Approve a plan and queue all eligible items.

    Validates the plan, validates all eligible items, calculates scheduling,
    creates ContentQueueJob records, and preserves existing generated scripts.

    Returns dict with:
    - plan_id
    - queued_count
    - skipped_count
    - schedule_summary
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject, ContentStatus
    from backend.plan_models import ContentPlan, PlanStatus
    from backend.queue_models import ContentQueueJob, QueueStatus

    db = SessionLocal()
    try:
        plan = db.query(ContentPlan).filter(ContentPlan.id == plan_id).first()
        if not plan:
            raise ValueError(f"Plan not found: {plan_id}")

        if plan.status != PlanStatus.DRAFT:
            raise ValueError(f"Plan must be in 'draft' status to approve. Current status: {plan.status}")

        # Use plan-level schedule or override from request
        effective_schedule_start = schedule_start or (plan.schedule_start.isoformat() if plan.schedule_start else None)
        effective_interval = schedule_interval_minutes or plan.schedule_interval_minutes or 1440  # Default 1 day
        effective_timezone = schedule_timezone or plan.schedule_timezone

        # Parse schedule if provided
        parsed_schedule_start = None
        if effective_schedule_start:
            from backend.services.scheduler import parse_schedule_time_with_zone, parse_schedule_time
            if effective_timezone:
                parsed_schedule_start = parse_schedule_time_with_zone(effective_schedule_start, effective_timezone)
            else:
                parsed_schedule_start = parse_schedule_time(effective_schedule_start)

        # Get all eligible projects (have script, not already queued)
        projects = db.query(ContentProject).filter(
            ContentProject.plan_id == plan_id,
            ContentProject.status.in_({ContentStatus.PENDING, ContentStatus.COMPLETED})
        ).order_by(ContentProject.created_at.asc()).all()

        if not projects:
            raise ValueError(f"No eligible items to queue in plan {plan_id}. Generate scripts first.")

        queued_count = 0
        skipped_count = 0
        schedule_summary = []

        for i, project in enumerate(projects):
            # Check if already queued
            existing_queue_job = db.query(ContentQueueJob).filter(
                ContentQueueJob.content_project_id == project.id
            ).first()
            if existing_queue_job:
                skipped_count += 1
                schedule_summary.append(f"Skipped (already queued): '{project.topic[:40]}'")
                continue

            # Calculate scheduled publish time
            scheduled_publish_at = None
            if parsed_schedule_start:
                scheduled_publish_at = parsed_schedule_start + timedelta(minutes=effective_interval * i)
                schedule_summary.append(
                    f"Video {i+1} '{project.topic[:40]}': "
                    f"{scheduled_publish_at.strftime('%Y-%m-%d %H:%M UTC')}"
                )
            else:
                schedule_summary.append(f"Video {i+1} '{project.topic[:40]}': no schedule")

            # Create ContentQueueJob
            queue_job = ContentQueueJob(
                id=str(uuid.uuid4()),
                topic=project.topic,
                language=project.language,
                tone=project.tone,
                target_duration_seconds=project.target_duration_seconds,
                scene_count=project.scene_count,
                status=QueueStatus.QUEUED,
                content_project_id=project.id,
                scheduled_publish_at=scheduled_publish_at,
                # Use defaults for YouTube settings (can be overridden later)
                youtube_privacy_status="private",
                youtube_category_id="22",
            )
            db.add(queue_job)
            queued_count += 1

        # Update plan status
        plan.status = PlanStatus.APPROVED
        db.commit()

        logger.info(
            "Plan approved: plan_id=%s queued=%d skipped=%d",
            plan_id, queued_count, skipped_count
        )

        return {
            "plan_id": plan_id,
            "queued_count": queued_count,
            "skipped_count": skipped_count,
            "schedule_summary": schedule_summary,
        }
    finally:
        db.close()


def reject_plan(plan_id: str) -> bool:
    """
    Reject a plan.

    Returns True if rejected, False if not found.
    """
    from backend.db import SessionLocal
    from backend.plan_models import ContentPlan, PlanStatus

    db = SessionLocal()
    try:
        plan = db.query(ContentPlan).filter(ContentPlan.id == plan_id).first()
        if not plan:
            return False

        plan.status = PlanStatus.REJECTED
        db.commit()
        logger.info("Rejected plan: id=%s", plan_id)
        return True
    finally:
        db.close()


def check_plan_completion(plan_id: str) -> dict:
    """
    Check if a plan is complete (all queued jobs have reached terminal completion).

    Returns dict with:
    - complete: True if all jobs completed/failed, False otherwise
    - total_jobs: total queue jobs for this plan
    - completed_jobs: count of completed jobs
    - failed_jobs: count of failed jobs
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject
    from backend.queue_models import ContentQueueJob, QueueStatus

    db = SessionLocal()
    try:
        # Get all ContentProjects in this plan
        projects = db.query(ContentProject).filter(
            ContentProject.plan_id == plan_id
        ).all()

        total_jobs = 0
        completed_jobs = 0
        failed_jobs = 0

        for project in projects:
            queue_job = db.query(ContentQueueJob).filter(
                ContentQueueJob.content_project_id == project.id
            ).first()
            if queue_job:
                total_jobs += 1
                if queue_job.status == QueueStatus.COMPLETED:
                    completed_jobs += 1
                elif queue_job.status == QueueStatus.FAILED:
                    failed_jobs += 1

        complete = (total_jobs > 0) and (completed_jobs + failed_jobs == total_jobs)

        if complete:
            # Update plan status to completed
            from backend.plan_models import ContentPlan
            plan = db.query(ContentPlan).filter(ContentPlan.id == plan_id).first()
            if plan and plan.status != "completed":
                plan.status = "completed"
                db.commit()
                logger.info("Plan marked as completed: id=%s", plan_id)

        return {
            "complete": complete,
            "total_jobs": total_jobs,
            "completed_jobs": completed_jobs,
            "failed_jobs": failed_jobs,
        }
    finally:
        db.close()


# ── Duplicate Protection ─────────────────────────────────────────────────────

def find_duplicate_topics_in_plan(plan_id: str, topics: list[str]) -> list[str]:
    """
    Find topics that are duplicates within the same plan.

    Returns list of duplicate topic strings.
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject

    if not topics:
        return []

    normalised = {t.lower().strip(): t for t in topics}

    db = SessionLocal()
    try:
        existing = db.query(ContentProject.topic).filter(
            ContentProject.plan_id == plan_id
        ).all()
        existing_topics = {row.topic.lower().strip() for row in existing}
        return [
            original for norm, original in normalised.items()
            if norm in existing_topics
        ]
    finally:
        db.close()


def find_duplicate_topics_globally(topics: list[str]) -> list[str]:
    """
    Find topics that are duplicates against existing ContentProject records.

    Returns list of duplicate topic strings.
    """
    from backend.db import SessionLocal
    from backend.content_models import ContentProject

    if not topics:
        return []

    normalised = {t.lower().strip(): t for t in topics}

    db = SessionLocal()
    try:
        existing = db.query(ContentProject.topic).all()
        existing_topics = {row.topic.lower().strip() for row in existing}
        return [
            original for norm, original in normalised.items()
            if norm in existing_topics
        ]
    finally:
        db.close()
