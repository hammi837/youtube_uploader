"""
Background visual configuration for video generation (Phase 3E.3).

Supports various background types:
- Solid color
- Gradient (existing default)
- Local image
- Local video clip
- Generated placeholder
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from backend.services.media.ffmpeg import check_ffmpeg, FFmpegNotFoundError


class BackgroundType(str, Enum):
    """Supported background types."""
    GRADIENT = "gradient"  # Existing gradient background (default)
    SOLID_COLOR = "solid_color"  # Solid color background
    LOCAL_IMAGE = "local_image"  # Local image file
    LOCAL_IMAGE_AUTO = "local_image_auto"  # Automatic local image selection (Phase 3F.1)
    LOCAL_VIDEO = "local_video"  # Local video clip
    LOCAL_VIDEO_AUTO = "local_video_auto"  # Automatic local video selection (Phase 3E.5)
    PLACEHOLDER = "placeholder"  # Generated placeholder background
    VISUAL_PROVIDER = "visual_provider"  # Visual provider abstraction (Phase 3F.4)


class BackgroundFit(str, Enum):
    """How to fit background content to aspect ratio."""
    COVER = "cover"  # Crop to fill (may clip edges)
    CONTAIN = "contain"  # Fit within bounds (may have letterbox)
    FILL = "fill"  # Stretch to fill (may distort)
    STRETCH = "stretch"  # Alias for fill


VALID_BACKGROUND_TYPES = {bt.value for bt in BackgroundType}
VALID_FIT_MODES = {bf.value for bf in BackgroundFit}
DEFAULT_BACKGROUND_TYPE = BackgroundType.GRADIENT
DEFAULT_FIT_MODE = BackgroundFit.COVER

# Supported image extensions
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}

# Supported video extensions
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

# Asset directory paths
ASSETS_DIR = Path("assets")
BACKGROUNDS_DIR = ASSETS_DIR / "backgrounds"
IMAGES_DIR = BACKGROUNDS_DIR / "images"
VIDEOS_DIR = BACKGROUNDS_DIR / "videos"


@dataclass
class BackgroundConfig:
    """Configuration for a scene background."""
    background_type: BackgroundType = DEFAULT_BACKGROUND_TYPE
    background_path: Optional[str] = None
    background_color: Optional[str] = None  # Hex color or CSS color name
    background_fit: BackgroundFit = DEFAULT_FIT_MODE
    overlay_opacity: float = 0.3  # Dark overlay opacity for text readability (0.0-1.0)
    # Phase 3E.4: Video-specific options
    background_loop: bool = True  # Loop video if shorter than scene duration
    background_start_time: float = 0.0  # Start time in seconds


def get_background_config(
    background_type: str = "gradient",
    background_path: Optional[str] = None,
    background_color: Optional[str] = None,
    background_fit: str = "cover",
    overlay_opacity: float = 0.3,
    background_loop: bool = True,
    background_start_time: float = 0.0,
) -> BackgroundConfig:
    """
    Create a background configuration with validation.

    Args:
        background_type: Type of background (gradient, solid_color, local_image, local_video, local_video_auto, placeholder)
        background_path: Path to local asset file
        background_color: Solid color for solid_color type
        background_fit: How to fit background content (cover, contain, fill)
        overlay_opacity: Dark overlay opacity (0.0-1.0)
        background_loop: Loop video if shorter than scene duration (Phase 3E.4)
        background_start_time: Start time in seconds (Phase 3E.4)

    Returns:
        BackgroundConfig with validated values.
        Falls back to gradient if invalid values provided.
    """
    # Validate background type
    try:
        bg_type = BackgroundType(background_type.lower())
    except ValueError:
        bg_type = DEFAULT_BACKGROUND_TYPE

    # Validate fit mode
    try:
        bg_fit = BackgroundFit(background_fit.lower())
    except ValueError:
        bg_fit = DEFAULT_FIT_MODE

    # Validate overlay opacity
    overlay_opacity = max(0.0, min(1.0, overlay_opacity))
    
    # Validate background start time
    background_start_time = max(0.0, background_start_time)

    # For local assets, validate path if provided
    if bg_type in (BackgroundType.LOCAL_IMAGE, BackgroundType.LOCAL_VIDEO) and background_path:
        path = Path(background_path)
        if not path.exists():
            # Fall back to gradient if local asset missing
            bg_type = DEFAULT_BACKGROUND_TYPE
            background_path = None
        else:
            # Validate file extension
            ext = path.suffix.lower()
            if bg_type == BackgroundType.LOCAL_IMAGE and ext not in SUPPORTED_IMAGE_EXTENSIONS:
                bg_type = DEFAULT_BACKGROUND_TYPE
                background_path = None
            elif bg_type == BackgroundType.LOCAL_VIDEO and ext not in SUPPORTED_VIDEO_EXTENSIONS:
                bg_type = DEFAULT_BACKGROUND_TYPE
                background_path = None

    return BackgroundConfig(
        background_type=bg_type,
        background_path=background_path,
        background_color=background_color,
        background_fit=bg_fit,
        overlay_opacity=overlay_opacity,
        background_loop=background_loop,
        background_start_time=background_start_time,
    )


def is_valid_background_type(background_type: str) -> bool:
    """Check if a background type string is valid."""
    return background_type.lower() in VALID_BACKGROUND_TYPES


def is_valid_background_path(background_path: str, background_type: str) -> bool:
    """
    Check if a background path is valid for the given type.

    Args:
        background_path: Path to check
        background_type: Type of background

    Returns:
        True if the path exists and has a valid extension for the type.
    """
    if not background_path:
        return False

    path = Path(background_path)
    if not path.exists():
        return False

    ext = path.suffix.lower()
    if background_type == BackgroundType.LOCAL_IMAGE.value:
        return ext in SUPPORTED_IMAGE_EXTENSIONS
    elif background_type == BackgroundType.LOCAL_VIDEO.value:
        return ext in SUPPORTED_VIDEO_EXTENSIONS

    return False


def list_available_backgrounds() -> dict[str, list[str]]:
    """
    List available local background assets.

    Returns:
        Dictionary with 'images' and 'videos' keys containing lists of available files.
    """
    backgrounds = {
        "images": [],
        "videos": [],
    }

    # List images
    if IMAGES_DIR.exists():
        for file in IMAGES_DIR.iterdir():
            if file.is_file() and file.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                backgrounds["images"].append(str(file))

    # List videos
    if VIDEOS_DIR.exists():
        for file in VIDEOS_DIR.iterdir():
            if file.is_file() and file.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
                backgrounds["videos"].append(str(file))

    return backgrounds


def get_default_background_config() -> BackgroundConfig:
    """Get the default background configuration (gradient)."""
    return BackgroundConfig(
        background_type=DEFAULT_BACKGROUND_TYPE,
        background_fit=DEFAULT_FIT_MODE,
        overlay_opacity=0.3,
    )
