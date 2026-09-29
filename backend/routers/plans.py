"""
routers/plans.py — Phase 3L content planning endpoints.

POST   /api/plans                     — Create content plan
GET    /api/plans                     — List content plans
GET    /api/plans/{plan_id}           — Get plan detail
PUT    /api/plans/{plan_id}           — Update plan
DELETE /api/plans/{plan_id}           — Delete plan

POST   /api/plans/{plan_id}/items     — Add content item to plan
GET    /api/plans/{plan_id}/items     — List items in plan
PUT    /api/plans/{plan_id}/items/{item_id} — Update item
DELETE /api/plans/{plan_id}/items/{item_id} — Delete item

POST   /api/plans/{plan_id}/generate  — Generate scripts for all items
POST   /api/plans/{plan_id}/approve   — Approve plan and queue all items
POST   /api/plans/{plan_id}/reject    — Reject plan

Security: no credentials, tokens, or secrets are exposed.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.plan_models import (
    ContentPlanCreate,
    ContentPlanResponse,
    ContentPlanUpdate,
    PlanApproveRequest,
    PlanApproveResponse,
    PlanItemCreate,
    PlanItemResponse,
    PlanItemUpdate,
    plan_to_response,
)
from backend.services.plan_service import (
    add_plan_item,
    approve_plan,
    check_plan_completion,
    create_plan,
    delete_plan,
    find_duplicate_topics_globally,
    find_duplicate_topics_in_plan,
    generate_plan_scripts,
    get_plan,
    list_plan_items,
    list_plans,
    reject_plan,
    remove_plan_item,
    update_plan,
    update_plan_item,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/plans", tags=["plans"])


# ── Plan CRUD ────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, response_model=ContentPlanResponse)
def create_plan_endpoint(
    body: ContentPlanCreate,
    db: Session = Depends(get_db),
) -> ContentPlanResponse:
    """Create a new content plan."""
    logger.info("POST /api/plans name='%s'", body.name)

    try:
        plan_id = create_plan(
            name=body.name,
            description=body.description,
            schedule_start=body.schedule_start,
            schedule_interval_minutes=body.schedule_interval_minutes,
            schedule_timezone=body.schedule_timezone,
        )
        plan_dict = get_plan(plan_id)
        if not plan_dict:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created plan",
            )
        return ContentPlanResponse(**plan_dict)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Failed to create plan")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create plan: {str(exc)}",
        )


@router.get("", response_model=list[ContentPlanResponse])
def list_plans_endpoint(db: Session = Depends(get_db)) -> list[ContentPlanResponse]:
    """List all content plans."""
    try:
        plans = list_plans()
        return [ContentPlanResponse(**p) for p in plans]
    except Exception as exc:
        logger.exception("Failed to list plans")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list plans: {str(exc)}",
        )


@router.get("/{plan_id}", response_model=ContentPlanResponse)
def get_plan_endpoint(
    plan_id: str,
    db: Session = Depends(get_db),
) -> ContentPlanResponse:
    """Get a content plan by ID."""
    plan_dict = get_plan(plan_id)
    if not plan_dict:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan not found: {plan_id}",
        )
    return ContentPlanResponse(**plan_dict)


@router.put("/{plan_id}", response_model=ContentPlanResponse)
def update_plan_endpoint(
    plan_id: str,
    body: ContentPlanUpdate,
    db: Session = Depends(get_db),
) -> ContentPlanResponse:
    """Update a content plan."""
    logger.info("PUT /api/plans/%s", plan_id)

    try:
        updated = update_plan(
            plan_id=plan_id,
            name=body.name,
            description=body.description,
            status=body.status,
            schedule_start=body.schedule_start,
            schedule_interval_minutes=body.schedule_interval_minutes,
            schedule_timezone=body.schedule_timezone,
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plan not found: {plan_id}",
            )
        plan_dict = get_plan(plan_id)
        if not plan_dict:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve updated plan",
            )
        return ContentPlanResponse(**plan_dict)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Failed to update plan")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update plan: {str(exc)}",
        )


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_plan_endpoint(
    plan_id: str,
    db: Session = Depends(get_db),
):
    """Delete a content plan."""
    logger.info("DELETE /api/plans/%s", plan_id)

    try:
        deleted = delete_plan(plan_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plan not found: {plan_id}",
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Failed to delete plan")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete plan: {str(exc)}",
        )


# ── Plan Items ────────────────────────────────────────────────────────────────

@router.post("/{plan_id}/items", status_code=status.HTTP_201_CREATED, response_model=PlanItemResponse)
def add_plan_item_endpoint(
    plan_id: str,
    body: PlanItemCreate,
    db: Session = Depends(get_db),
) -> PlanItemResponse:
    """Add a content item to a plan."""
    logger.info("POST /api/plans/%s/items topic='%s'", plan_id, body.topic)

    try:
        # Check for duplicate topics within the plan
        duplicates = find_duplicate_topics_in_plan(plan_id, [body.topic])
        if duplicates:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate topic in plan: {', '.join(duplicates)}",
            )

        # Check for duplicate topics globally (optional, may be too restrictive)
        # Uncomment if you want global duplicate protection:
        # global_duplicates = find_duplicate_topics_globally([body.topic])
        # if global_duplicates:
        #     raise HTTPException(
        #         status_code=status.HTTP_409_CONFLICT,
        #         detail=f"Duplicate topic (already exists): {', '.join(global_duplicates)}",
        #     )

        item_id = add_plan_item(
            plan_id=plan_id,
            topic=body.topic,
            language=body.language,
            tone=body.tone,
            target_duration_seconds=body.target_duration_seconds,
            scene_count=body.scene_count,
        )

        # Get the created item
        items = list_plan_items(plan_id)
        item_dict = next((i for i in items if i["id"] == item_id), None)
        if not item_dict:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve created item",
            )
        return PlanItemResponse(**item_dict)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to add plan item")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add plan item: {str(exc)}",
        )


@router.get("/{plan_id}/items", response_model=list[PlanItemResponse])
def list_plan_items_endpoint(
    plan_id: str,
    db: Session = Depends(get_db),
) -> list[PlanItemResponse]:
    """List all items in a plan."""
    try:
        items = list_plan_items(plan_id)
        return [PlanItemResponse(**i) for i in items]
    except Exception as exc:
        logger.exception("Failed to list plan items")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list plan items: {str(exc)}",
        )


@router.put("/{plan_id}/items/{item_id}", response_model=PlanItemResponse)
def update_plan_item_endpoint(
    plan_id: str,
    item_id: str,
    body: PlanItemUpdate,
    db: Session = Depends(get_db),
) -> PlanItemResponse:
    """Update a plan item."""
    logger.info("PUT /api/plans/%s/items/%s", plan_id, item_id)

    try:
        updated = update_plan_item(
            item_id=item_id,
            topic=body.topic,
            language=body.language,
            tone=body.tone,
            target_duration_seconds=body.target_duration_seconds,
            scene_count=body.scene_count,
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Item not found: {item_id}",
            )

        # Get the updated item
        items = list_plan_items(plan_id)
        item_dict = next((i for i in items if i["id"] == item_id), None)
        if not item_dict:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve updated item",
            )
        return PlanItemResponse(**item_dict)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Failed to update plan item")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update plan item: {str(exc)}",
        )


@router.delete("/{plan_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def remove_plan_item_endpoint(
    plan_id: str,
    item_id: str,
    db: Session = Depends(get_db),
):
    """Remove a plan item."""
    logger.info("DELETE /api/plans/%s/items/%s", plan_id, item_id)

    try:
        removed = remove_plan_item(item_id)
        if not removed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Item not found: {item_id}",
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Failed to remove plan item")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove plan item: {str(exc)}",
        )


# ── Script Generation ─────────────────────────────────────────────────────────

@router.post("/{plan_id}/generate")
def generate_plan_scripts_endpoint(
    plan_id: str,
    db: Session = Depends(get_db),
):
    """Generate scripts for all planned items in a plan."""
    logger.info("POST /api/plans/%s/generate", plan_id)

    try:
        result = generate_plan_scripts(plan_id)
        return result
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Failed to generate plan scripts")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate plan scripts: {str(exc)}",
        )


# ── Plan Approval ────────────────────────────────────────────────────────────

@router.post("/{plan_id}/approve", response_model=PlanApproveResponse)
def approve_plan_endpoint(
    plan_id: str,
    body: PlanApproveRequest,
    db: Session = Depends(get_db),
) -> PlanApproveResponse:
    """Approve a plan and queue all eligible items."""
    logger.info("POST /api/plans/%s/approve", plan_id)

    try:
        result = approve_plan(
            plan_id=plan_id,
            schedule_start=body.schedule_start,
            schedule_interval_minutes=body.schedule_interval_minutes,
            schedule_timezone=body.schedule_timezone,
        )
        return PlanApproveResponse(**result)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Failed to approve plan")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to approve plan: {str(exc)}",
        )


@router.post("/{plan_id}/reject")
def reject_plan_endpoint(
    plan_id: str,
    db: Session = Depends(get_db),
):
    """Reject a plan."""
    logger.info("POST /api/plans/%s/reject", plan_id)

    try:
        rejected = reject_plan(plan_id)
        if not rejected:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plan not found: {plan_id}",
            )
        return {"plan_id": plan_id, "status": "rejected"}
    except Exception as exc:
        logger.exception("Failed to reject plan")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject plan: {str(exc)}",
        )


@router.get("/{plan_id}/completion")
def check_plan_completion_endpoint(
    plan_id: str,
    db: Session = Depends(get_db),
):
    """Check if a plan is complete (all queued jobs have reached terminal completion)."""
    try:
        result = check_plan_completion(plan_id)
        return result
    except Exception as exc:
        logger.exception("Failed to check plan completion")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check plan completion: {str(exc)}",
        )
