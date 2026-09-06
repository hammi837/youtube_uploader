"""
backend/services/media/ffmpeg.py — FFmpeg path resolution and health check.

Reads FFMPEG_PATH (and optionally FFPROBE_PATH) from the environment/.env
and exposes simple helpers that the rest of the application can call to:

  - resolve the absolute path to ffmpeg.exe / ffprobe.exe
  - verify the executable exists and is runnable
  - retrieve the version string

This module does NOT implement any video-assembly logic (Phase 2D).
It is intentionally lightweight so it can be imported without side-effects.

Configuration (.env):
    FFMPEG_PATH   — absolute path to ffmpeg.exe
                    default: G:\\youtube-uploader\\tools\\ffmpeg\\bin\\ffmpeg.exe
    FFPROBE_PATH  — absolute path to ffprobe.exe
                    default: derived from FFMPEG_PATH (same directory)

Installation location:
    G:\\youtube-uploader\\tools\\ffmpeg\\bin\\ffmpeg.exe
    G:\\youtube-uploader\\tools\\ffmpeg\\bin\\ffprobe.exe
    G:\\youtube-uploader\\tools\\ffmpeg\\bin\\ffplay.exe

These binaries are excluded from Git via .gitignore (tools/).
They are never written to C: — see README.md § FFmpeg.

Security:
    - No credential, token, or filesystem secret is exposed in any return value.
    - Subprocess output is captured and truncated before being returned.
    - stderr from ffmpeg -version is intentionally discarded (it is empty on
      success; on failure the CalledProcessError message is caught and wrapped).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
_DEFAULT_FFMPEG_PATH = r"G:\youtube-uploader\tools\ffmpeg\bin\ffmpeg.exe"
_DEFAULT_FFPROBE_PATH = r"G:\youtube-uploader\tools\ffmpeg\bin\ffprobe.exe"

# Version output is truncated to this many characters for safety
_VERSION_MAX_LEN = 200


# ── Exceptions ────────────────────────────────────────────────────────────────

class FFmpegNotFoundError(Exception):
    """
    Raised when the configured FFmpeg executable cannot be found or executed.

    Callers should catch this and return HTTP 503 / raise a clear user-facing
    error rather than letting it propagate as an unhandled 500.
    """


# ── Public API ────────────────────────────────────────────────────────────────

def get_ffmpeg_path() -> str:
    """
    Return the absolute path to ffmpeg.exe from the environment.

    Reads FFMPEG_PATH from the environment (loaded from .env by the
    application startup).  Falls back to the default project-local path
    if the variable is not set.

    Returns:
        Absolute path string (may or may not exist on disk — call
        check_ffmpeg() to verify).
    """
    path = os.getenv("FFMPEG_PATH", _DEFAULT_FFMPEG_PATH).strip()
    logger.debug("FFmpeg path resolved: %s", path)
    return path


def get_ffprobe_path() -> str:
    """
    Return the absolute path to ffprobe.exe from the environment.

    Derives the default from FFMPEG_PATH's directory when FFPROBE_PATH
    is not explicitly set.
    """
    explicit = os.getenv("FFPROBE_PATH", "").strip()
    if explicit:
        return explicit
    # Derive from FFMPEG_PATH: same directory, different binary name
    ffmpeg_dir = Path(get_ffmpeg_path()).parent
    return str(ffmpeg_dir / "ffprobe.exe")


def get_ffmpeg_version() -> str:
    """
    Return the first line of `ffmpeg -version` output.

    Returns:
        Version string, e.g. "ffmpeg version N-126416-g9997fd0606-20260905 ..."
        Truncated to _VERSION_MAX_LEN characters.

    Raises:
        FFmpegNotFoundError: executable missing, not runnable, or timed out.
    """
    path = get_ffmpeg_path()
    _assert_executable_exists(path)

    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        first_line = result.stdout.splitlines()[0] if result.stdout.strip() else ""
        if not first_line:
            raise FFmpegNotFoundError(
                f"FFmpeg at '{path}' produced no version output. "
                "The binary may be corrupt."
            )
        return first_line[:_VERSION_MAX_LEN]

    except FileNotFoundError as exc:
        raise FFmpegNotFoundError(
            f"FFmpeg executable not found at configured path: '{path}'. "
            "Set FFMPEG_PATH in backend/.env and verify the file exists."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegNotFoundError(
            f"FFmpeg at '{path}' timed out after 10 seconds. "
            "The binary may be broken or the system is under heavy load."
        ) from exc
    except OSError as exc:
        raise FFmpegNotFoundError(
            f"Cannot execute FFmpeg at '{path}': {exc}"
        ) from exc


def check_ffmpeg() -> dict:
    """
    Verify that FFmpeg is correctly configured and runnable.

    Returns a dict that is safe to include in a health-check response:
    {
        "available": True | False,
        "path": "<absolute path>",          # never contains secrets
        "version": "<first version line>",  # only when available=True
        "error": "<message>",               # only when available=False
    }

    Never raises — always returns a dict.  The caller decides whether to
    surface the error to the user.
    """
    path = get_ffmpeg_path()
    try:
        version = get_ffmpeg_version()
        logger.info("FFmpeg check OK: %s", version)
        return {
            "available": True,
            "path": path,
            "version": version,
        }
    except FFmpegNotFoundError as exc:
        msg = str(exc)
        logger.warning("FFmpeg check failed: %s", msg)
        return {
            "available": False,
            "path": path,
            "error": msg,
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _assert_executable_exists(path: str) -> None:
    """
    Raise FFmpegNotFoundError if the path does not point to an existing file.

    Does NOT attempt to run the binary — purely a filesystem check.
    """
    p = Path(path)
    if not p.exists():
        raise FFmpegNotFoundError(
            f"FFmpeg executable not found at: '{path}'. "
            "Download FFmpeg and set FFMPEG_PATH in backend/.env. "
            "See README.md § FFmpeg for instructions."
        )
    if not p.is_file():
        raise FFmpegNotFoundError(
            f"FFMPEG_PATH points to a directory, not a file: '{path}'."
        )
