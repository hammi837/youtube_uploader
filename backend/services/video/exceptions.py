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


class YouTubeAuthError(Exception):
    """
    Raised when YouTube OAuth credentials are invalid or have been revoked.

    Specifically catches ``invalid_grant`` from Google's token endpoint.
    This is a permanent failure — the same refresh token will not work again.

    The caller (queue_processor) should:
      1. Mark the job as awaiting_auth (not retried with the same token).
      2. Preserve the generated video, thumbnail, and captions.
      3. Guide the user through the reauthorization flow.
    """

