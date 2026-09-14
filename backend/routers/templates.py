"""
routers/templates.py — Phase 3E.1 Template API endpoints.

GET    /api/templates — list available video templates
GET    /api/templates/{template_id} — get template details

Security: no credentials exposed in responses.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.services.video.templates import (
    get_template,
    list_templates,
    is_valid_template,
    get_default_template,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/templates", tags=["templates"])


# ── GET /api/templates ───────────────────────────────────────────────────────

@router.get("", response_model=list[dict])
def list_templates_endpoint() -> list[dict]:
    """List all available video templates."""
    templates = list_templates()
    return [
        {
            "template_id": t.template_id,
            "name": t.name,
            "description": t.description,
            "background_type": t.background_type,
            "layout_type": t.layout_type,
            "typography_style": t.typography_style,
            "caption_style": t.caption_style,
            "supports_animation": t.supports_animation,
        }
        for t in templates
    ]


# ── GET /api/templates/{template_id} ─────────────────────────────────────

@router.get("/{template_id}", response_model=dict)
def get_template_endpoint(template_id: str) -> dict:
    """Get template details by ID."""
    if not is_valid_template(template_id):
        raise HTTPException(
            status_code=404,
            detail=f"Template not found: {template_id}"
        )
    
    template = get_template(template_id)
    return {
        "template_id": template.template_id,
        "name": template.name,
        "description": template.description,
        "background_type": template.background_type,
        "layout_type": template.layout_type,
        "typography_style": template.typography_style,
        "caption_style": template.caption_style,
        "supports_animation": template.supports_animation,
    }
