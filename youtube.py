"""
youtube.py — YouTube Data API v3 service layer.

Supports:
  - Resumable chunked upload with progress callback and tqdm (CLI)
  - Scheduled publishing via status.publishAt (ISO 8601 UTC)
  - Retry logic for transient HTTP errors (exponential backoff)
  - videos.update  — title, description, tags, privacy, reschedule, cancel schedule
  - thumbnails.set — upload a custom thumbnail after video upload
  - Upload cancellation — DELETE the resumable upload URI
"""

from __future__ import annotations

import os
import time
import http.client
import httplib2
from datetime import datetime, timezone
from typing import Callable, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from tqdm import tqdm

from auth import get_credentials

# ── Constants ─────────────────────────────────────────────────────────────────
API_SERVICE_NAME = "youtube"
API_VERSION      = "v3"

RETRIABLE_STATUS_CODES = {500, 502, 503, 504}
RETRIABLE_EXCEPTIONS   = (
    httplib2.HttpLib2Error,
    IOError,
    http.client.NotConnected,
    http.client.IncompleteRead,
    http.client.ImproperConnectionState,
    http.client.CannotSendRequest,
    http.client.CannotSendHeader,
    http.client.ResponseNotReady,
    http.client.BadStatusLine,
)

MAX_RETRIES = 10

# Default chunk size: 8 MB — good balance for most connections.
# YouTube recommends multiples of 256 KB; min recommended is 256 KB.
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024   # 8 MB
MIN_CHUNK_SIZE     = 256 * 1024        # 256 KB


# ── Service builder ────────────────────────────────────────────────────────────

def build_service():
    """Return an authenticated YouTube API service client."""
    creds = get_credentials()
    return build(API_SERVICE_NAME, API_VERSION, credentials=creds)


# ── Upload ─────────────────────────────────────────────────────────────────────

def upload_video(
    file_path: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category_id: str = "22",
    privacy_status: str = "private",
    publish_at: datetime | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """
    Upload a video file to YouTube.

    Args:
        file_path:          Local path to the video file.
        title:              Video title (max 100 chars).
        description:        Video description (max 5000 chars).
        tags:               List of tag strings.
        category_id:        YouTube category ID string.
        privacy_status:     'private', 'unlisted', or 'public'.
                            Overridden to 'private' when publish_at is set.
        publish_at:         Timezone-aware UTC datetime for scheduled publishing.
        chunk_size:         Upload chunk size in bytes (default 8 MB).
        progress_callback:  Optional callable(bytes_uploaded, total_bytes).
                            Called after each chunk. Use for non-CLI progress.

    Returns:
        dict with keys: video_id, title, url, scheduled_at (or None)

    Raises:
        FileNotFoundError: Video file not found.
        HttpError:         Non-retriable API error.
        ValueError:        Invalid chunk size.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found: {file_path}")

    chunk_size = max(chunk_size, MIN_CHUNK_SIZE)
    # Align to 256 KB boundary (YouTube requirement for resumable uploads)
    chunk_size = (chunk_size // MIN_CHUNK_SIZE) * MIN_CHUNK_SIZE

    service = build_service()

    effective_privacy = "private" if publish_at else privacy_status

    status_block: dict = {"privacyStatus": effective_privacy}
    if publish_at:
        status_block["publishAt"] = _format_publish_at(publish_at)

    body = {
        "snippet": {
            "title":       title[:100],
            "description": description[:5000],
            "tags":        tags or [],
            "categoryId":  category_id,
        },
        "status": status_block,
    }

    media = MediaFileUpload(
        file_path,
        chunksize=chunk_size,
        resumable=True,
        mimetype="video/*",
    )

    request = service.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    file_size = os.path.getsize(file_path)

    if progress_callback:
        # Headless mode — use callback (for FastAPI)
        video_id = _resumable_upload_headless(request, file_size, progress_callback)
    else:
        # CLI mode — show tqdm progress bar
        print(f"\nUploading: {os.path.basename(file_path)} ({file_size / 1024 / 1024:.1f} MB)")
        if publish_at:
            print(f"Scheduled publish time: {publish_at.strftime('%Y-%m-%d %H:%M UTC')}")
        video_id = _resumable_upload_tqdm(request, file_size)
        print(f"\nVideo uploaded successfully!")
        print(f"Video ID: {video_id}")
        if publish_at:
            print(f"Scheduled publish time: {publish_at.strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"(Video is private until then)")
        print(f"Watch at: https://www.youtube.com/watch?v={video_id}")

    return {
        "video_id":     video_id,
        "title":        title,
        "url":          f"https://www.youtube.com/watch?v={video_id}",
        "scheduled_at": publish_at.isoformat() if publish_at else None,
    }


# ── Update ─────────────────────────────────────────────────────────────────────

def update_video(
    video_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    privacy_status: str | None = None,
    publish_at: datetime | None = None,
    clear_schedule: bool = False,
) -> dict:
    """
    Update metadata for an existing video.

    To reschedule: pass a new publish_at (video must still be private/never published).
    To cancel schedule: pass clear_schedule=True (video stays private permanently).

    Args:
        video_id:       YouTube video ID.
        title:          New title (or None to leave unchanged).
        description:    New description (or None to leave unchanged).
        tags:           New tags list (or None to leave unchanged).
        privacy_status: New privacy ('private'|'unlisted'|'public'), or None.
        publish_at:     New scheduled UTC datetime, or None.
        clear_schedule: If True, removes the publishAt and keeps video private.

    Returns:
        The updated video resource dict from the API.

    Raises:
        HttpError: API error (e.g. trying to reschedule a published video).
    """
    service = build_service()

    # Fetch current state first
    current = service.videos().list(
        part="snippet,status",
        id=video_id,
    ).execute()

    if not current.get("items"):
        raise ValueError(f"Video not found: {video_id}")

    item     = current["items"][0]
    snippet  = item["snippet"]
    status   = item["status"]

    # Apply updates, keeping existing values where not specified
    new_snippet = {
        "title":          (title[:100] if title is not None else snippet["title"]),
        "description":    (description[:5000] if description is not None else snippet.get("description", "")),
        "tags":           (tags if tags is not None else snippet.get("tags", [])),
        "categoryId":     snippet.get("categoryId", "22"),
    }

    new_status: dict = {}

    if clear_schedule:
        # Remove scheduled publish — keep permanently private
        new_status["privacyStatus"] = "private"
        new_status["publishAt"]     = None
    elif publish_at is not None:
        # Reschedule: must re-send privacyStatus=private per API requirement
        new_status["privacyStatus"] = "private"
        new_status["publishAt"]     = _format_publish_at(publish_at)
    elif privacy_status is not None:
        new_status["privacyStatus"] = privacy_status
    else:
        new_status["privacyStatus"] = status.get("privacyStatus", "private")

    body = {
        "id":      video_id,
        "snippet": new_snippet,
        "status":  new_status,
    }

    response = service.videos().update(
        part="snippet,status",
        body=body,
    ).execute()

    return response


# ── Thumbnail ──────────────────────────────────────────────────────────────────

def set_thumbnail(video_id: str, image_path: str) -> dict:
    """
    Upload a custom thumbnail image for a video.

    Args:
        video_id:   YouTube video ID.
        image_path: Local path to the thumbnail image (JPEG or PNG).
                    Recommended: 1280×720, max 2 MB.

    Returns:
        The thumbnails resource dict from the API.

    Raises:
        FileNotFoundError: Thumbnail file not found.
        HttpError:         API error. Common cause: channel not verified
                           (custom thumbnails require a verified channel).
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Thumbnail file not found: {image_path}")

    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"

    service = build_service()

    with open(image_path, "rb") as fh:
        media = MediaIoBaseUpload(fh, mimetype=mime, resumable=False)
        response = service.thumbnails().set(
            videoId=video_id,
            media_body=media,
        ).execute()

    return response


# ── Cancel upload ──────────────────────────────────────────────────────────────

def cancel_resumable_upload(upload_uri: str) -> bool:
    """
    Cancel an active resumable upload by sending a DELETE to the upload URI.

    Args:
        upload_uri: The upload URI returned when the resumable upload was initiated.

    Returns:
        True if cancelled (HTTP 499), False otherwise.
    """
    import urllib.request
    try:
        req = urllib.request.Request(upload_uri, method="DELETE")
        with urllib.request.urlopen(req) as resp:
            return resp.status in (200, 204, 499)
    except Exception:
        return False


# ── Internal helpers ───────────────────────────────────────────────────────────

def _format_publish_at(dt: datetime) -> str:
    """Format a UTC datetime as YouTube-compatible RFC 3339 string."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _resumable_upload_tqdm(request, file_size: int) -> str:
    """Run a resumable upload with a tqdm progress bar (CLI use)."""
    response = None
    error    = None
    retry    = 0

    with tqdm(total=file_size, unit="B", unit_scale=True, desc="Progress") as pbar:
        last_progress = 0
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    current = int(status.resumable_progress)
                    pbar.update(current - last_progress)
                    last_progress = current
                if response is not None:
                    pbar.update(file_size - last_progress)
            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    error = f"Retriable HTTP error {e.resp.status}: {e.content}"
                else:
                    raise
            except RETRIABLE_EXCEPTIONS as e:
                error = f"Retriable error: {e}"

            if error:
                retry += 1
                if retry > MAX_RETRIES:
                    raise RuntimeError(f"Max retries exceeded. Last error: {error}")
                sleep_sec = min(2 ** retry, 64)
                print(f"\n{error} — retrying in {sleep_sec}s ({retry}/{MAX_RETRIES})")
                time.sleep(sleep_sec)
                error = None

    return response["id"]


def _resumable_upload_headless(
    request,
    file_size: int,
    progress_callback: Callable[[int, int], None],
) -> str:
    """Run a resumable upload with a callback for progress (FastAPI use)."""
    response = None
    error    = None
    retry    = 0

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress_callback(int(status.resumable_progress), file_size)
            if response is not None:
                progress_callback(file_size, file_size)
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = f"Retriable HTTP error {e.resp.status}: {e.content}"
            else:
                raise
        except RETRIABLE_EXCEPTIONS as e:
            error = f"Retriable error: {e}"

        if error:
            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError(f"Max retries exceeded. Last error: {error}")
            time.sleep(min(2 ** retry, 64))
            error = None

    return response["id"]
