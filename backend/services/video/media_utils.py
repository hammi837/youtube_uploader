"""
backend/services/video/media_utils.py — shared path helpers and data dirs.

All paths are on G: — never on C:.
"""

from __future__ import annotations

import os
from pathlib import Path


# ── Base data directory ───────────────────────────────────────────────────────
# Configurable via DATA_DIR env var; defaults to project data/ folder.

def get_data_dir() -> Path:
    raw = os.getenv("DATA_DIR", r"G:\youtube-uploader\data")
    return Path(raw)


def get_videos_dir() -> Path:
    d = get_data_dir() / "videos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_thumbnails_dir() -> Path:
    d = get_data_dir() / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_captions_dir() -> Path:
    d = get_data_dir() / "captions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_temp_dir(job_id: str) -> Path:
    """Return a per-job temp directory, creating it if needed."""
    d = get_data_dir() / "temp" / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_assets_music_dir() -> Path:
    d = get_data_dir() / "assets" / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_assets_images_dir() -> Path:
    d = get_data_dir() / "assets" / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_whisper_models_dir() -> Path:
    d = get_data_dir() / "models" / "whisper"
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_video_path(job_id: str) -> Path:
    return get_videos_dir() / f"{job_id}.mp4"


def output_thumbnail_path(job_id: str) -> Path:
    return get_thumbnails_dir() / f"{job_id}.jpg"


def output_caption_path(job_id: str) -> Path:
    return get_captions_dir() / f"{job_id}.srt"


def cleanup_temp(job_id: str, keep_on_failure: bool = False) -> None:
    """
    Remove the per-job temp directory.

    On failure keep_on_failure=True preserves files for debugging,
    but only logs a warning rather than raising.
    """
    import shutil
    import logging
    logger = logging.getLogger(__name__)

    temp = get_data_dir() / "temp" / job_id
    if not temp.exists():
        return
    if keep_on_failure:
        logger.warning("Keeping temp dir for debugging: %s", temp)
        return
    try:
        shutil.rmtree(temp)
        logger.debug("Cleaned up temp dir: %s", temp)
    except OSError as exc:
        logger.warning("Could not clean temp dir %s: %s", temp, exc)


def find_music_file() -> Path | None:
    """Return the first music file found in the assets/music directory."""
    music_dir = get_assets_music_dir()
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        files = list(music_dir.glob(ext))
        if files:
            return files[0]
    return None


def safe_path_for_job(job_id: str, base_dir: Path, extension: str) -> Path:
    """
    Build a safe output path within base_dir.
    Raises ValueError if job_id contains path-traversal characters.
    """
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise ValueError(f"Invalid job_id: '{job_id}'")
    return base_dir / f"{job_id}{extension}"
