"""
backend/routers/aspect_ratios.py — Phase 3E.2 aspect ratio API endpoints.

GET    /api/aspect-ratios      — list all available aspect ratios
GET    /api/aspect-ratios/{id} — get specific aspect ratio details
"""

import logging

from fastapi import APIRouter, HTTPException, status
from backend.services.video.aspect_ratio import (
    list_aspect_ratios,
    get_aspect_ratio_config,
    is_valid_aspect_ratio,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/aspect-ratios", tags=["aspect-ratios"])


@router.get("", response_model=list[dict])
def list_aspect_ratios_endpoint():
    """
    List all available aspect ratios.

    Returns a list of aspect ratio configurations with:
    - aspect_ratio: The ratio string (e.g., "16:9")
    - width: Width in pixels
    - height: Height in pixels
    - label: Human-readable label
    - description: Description of the format
    """
    return list_aspect_ratios()


@router.get("/{aspect_ratio}", response_model=dict)
def get_aspect_ratio_details(aspect_ratio: str):
    """
    Get details for a specific aspect ratio.

    Returns configuration details for the requested aspect ratio.
    Returns 404 if the aspect ratio is not valid.
    """
    if not is_valid_aspect_ratio(aspect_ratio):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invalid aspect ratio: {aspect_ratio}. Valid ratios: 16:9, 9:16",
        )

    config = get_aspect_ratio_config(aspect_ratio)
    return {
        "aspect_ratio": config.aspect_ratio,
        "width": config.width,
        "height": config.height,
        "label": config.label,
        "description": config.description,
    }
