"""
backend/routers/backgrounds.py — Phase 3E.3/3E.4 background visual API endpoints.

GET    /api/backgrounds      — list available local background assets
GET    /api/backgrounds/types — list supported background types
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from backend.services.video.background_config import (
    list_available_backgrounds,
    VALID_BACKGROUND_TYPES,
    VALID_FIT_MODES,
    BackgroundType,
    DEFAULT_BACKGROUND_TYPE,
    DEFAULT_FIT_MODE,
)
from backend.services.video.video_asset_discovery import discover_video_assets

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backgrounds", tags=["backgrounds"])


@router.get("", response_model=dict)
def list_backgrounds_endpoint():
    """
    List all available local background assets.

    Returns a dictionary with 'images' and 'videos' keys containing
    lists of available files in the assets/backgrounds directory.
    """
    backgrounds = list_available_backgrounds()
    
    # Add video metadata
    videos = discover_video_assets()
    backgrounds["videos"] = [
        {
            "path": v.path,
            "filename": v.filename,
            "duration": v.duration,
            "width": v.width,
            "height": v.height,
            "orientation": v.orientation,
        }
        for v in videos
    ]
    
    return backgrounds


@router.get("/types", response_model=dict)
def list_background_types_endpoint():
    """
    List all supported background types and fit modes.

    Returns available background types and fit modes for UI rendering.
    """
    return {
        "background_types": list(VALID_BACKGROUND_TYPES),
        "fit_modes": list(VALID_FIT_MODES),
        "default_type": DEFAULT_BACKGROUND_TYPE.value,
        "default_fit": DEFAULT_FIT_MODE.value,
    }
