"""
routers/youtube_playlists.py — Phase 3D YouTube playlist endpoints.

GET    /api/youtube/playlists     — list user's playlists
POST   /api/youtube/playlists/refresh — force refresh playlist cache

Security: no credentials exposed in responses.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.queue_models import YouTubePlaylist, YouTubePlaylistResponse
import youtube as yt_core

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/youtube", tags=["youtube"])


# ── GET /api/youtube/playlists ───────────────────────────────────────────────

@router.get("/playlists", response_model=list[YouTubePlaylistResponse])
def list_playlists(db: Session = Depends(get_db)):
    """
    List the user's YouTube playlists.
    
    Returns cached playlists if available, otherwise fetches from YouTube API.
    Cache is refreshed if older than 1 hour.
    """
    # Check if we have recent cached playlists (less than 1 hour old)
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    cached = db.query(YouTubePlaylist).filter(
        YouTubePlaylist.cached_at >= one_hour_ago
    ).order_by(YouTubePlaylist.title).all()
    
    if cached:
        logger.info("Returning %d cached playlists", len(cached))
        return [YouTubePlaylistResponse.model_validate(p) for p in cached]
    
    # Fetch fresh playlists from YouTube API
    try:
        playlists_data = yt_core.list_playlists()
        
        # Clear old cache and insert fresh data
        db.query(YouTubePlaylist).delete()
        
        for playlist in playlists_data:
            db.add(YouTubePlaylist(
                id=playlist["id"],
                title=playlist["title"],
                description=playlist.get("description", ""),
                item_count=playlist["item_count"],
                cached_at=datetime.now(timezone.utc),
            ))
        
        db.commit()
        logger.info("Fetched and cached %d playlists from YouTube API", len(playlists_data))
        
        # Return the freshly cached data
        fresh = db.query(YouTubePlaylist).order_by(YouTubePlaylist.title).all()
        return [YouTubePlaylistResponse.model_validate(p) for p in fresh]
        
    except Exception as exc:
        logger.error("Failed to fetch playlists from YouTube API: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch playlists: {str(exc)}"
        )


# ── POST /api/youtube/playlists/refresh ─────────────────────────────────────

@router.post("/playlists/refresh", response_model=list[YouTubePlaylistResponse])
def refresh_playlists(db: Session = Depends(get_db)):
    """
    Force refresh the playlist cache from YouTube API.
    
    Use this after creating new playlists in YouTube Studio to make them
    available for selection in the queue UI.
    """
    try:
        playlists_data = yt_core.list_playlists()
        
        # Clear cache and insert fresh data
        db.query(YouTubePlaylist).delete()
        
        for playlist in playlists_data:
            db.add(YouTubePlaylist(
                id=playlist["id"],
                title=playlist["title"],
                description=playlist.get("description", ""),
                item_count=playlist["item_count"],
                cached_at=datetime.now(timezone.utc),
            ))
        
        db.commit()
        logger.info("Force refreshed %d playlists from YouTube API", len(playlists_data))
        
        fresh = db.query(YouTubePlaylist).order_by(YouTubePlaylist.title).all()
        return [YouTubePlaylistResponse.model_validate(p) for p in fresh]
        
    except Exception as exc:
        logger.error("Failed to refresh playlists from YouTube API: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh playlists: {str(exc)}"
        )
