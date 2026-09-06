"""
backend/services/video/exceptions.py — typed exceptions for Phase 2D.
"""


class VideoGenerationError(Exception):
    """Base class for all video-generation errors."""


class FFmpegError(VideoGenerationError):
    """FFmpeg process failed or is unavailable."""


class MediaNotFoundError(VideoGenerationError):
    """A required media file (audio, image, etc.) does not exist."""


class InvalidVideoError(VideoGenerationError):
    """The assembled video fails validation (wrong resolution, duration, etc.)."""


class CaptionGenerationError(VideoGenerationError):
    """Caption / subtitle generation failed."""


class ThumbnailGenerationError(VideoGenerationError):
    """Thumbnail generation failed."""


class PipelineConfigError(VideoGenerationError):
    """The pipeline is misconfigured (missing project, script, FFmpeg, etc.)."""
