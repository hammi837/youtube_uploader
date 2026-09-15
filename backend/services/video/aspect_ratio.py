"""
Aspect ratio configuration for video generation (Phase 3E.2).

Supports 16:9 (landscape) and 9:16 (vertical) video formats.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class AspectRatioConfig:
    """Configuration for a specific aspect ratio."""
    aspect_ratio: str
    width: int
    height: int
    label: str
    description: str
    horizontal_margin: float = 3.0  # Percentage of width for horizontal margins
    vertical_margin: float = 3.0   # Percentage of height for vertical margins


# Valid aspect ratios
VALID_ASPECT_RATIOS: list[str] = ["16:9", "9:16"]

# Default aspect ratio (backward compatibility)
DEFAULT_ASPECT_RATIO: str = "16:9"

# Aspect ratio configurations
ASPECT_RATIO_CONFIGS: dict[str, AspectRatioConfig] = {
    "16:9": AspectRatioConfig(
        aspect_ratio="16:9",
        width=1920,
        height=1080,
        label="YouTube Landscape",
        description="Standard YouTube video format (1920x1080)"
    ),
    "9:16": AspectRatioConfig(
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        label="YouTube Shorts",
        description="Vertical video format for YouTube Shorts (1080x1920)"
    ),
}


def get_aspect_ratio_config(aspect_ratio: str) -> AspectRatioConfig:
    """
    Get configuration for a specific aspect ratio.
    
    Args:
        aspect_ratio: Aspect ratio string (e.g., "16:9" or "9:16")
    
    Returns:
        AspectRatioConfig for the requested ratio.
        Falls back to 16:9 if the ratio is invalid.
    """
    if aspect_ratio not in ASPECT_RATIO_CONFIGS:
        # Fallback to default for invalid ratios
        return ASPECT_RATIO_CONFIGS[DEFAULT_ASPECT_RATIO]
    return ASPECT_RATIO_CONFIGS[aspect_ratio]


def get_dimensions(aspect_ratio: str) -> tuple[int, int]:
    """
    Get width and height for a specific aspect ratio.
    
    Args:
        aspect_ratio: Aspect ratio string (e.g., "16:9" or "9:16")
    
    Returns:
        Tuple of (width, height) in pixels.
        Falls back to (1920, 1080) if the ratio is invalid.
    """
    config = get_aspect_ratio_config(aspect_ratio)
    return config.width, config.height


def is_valid_aspect_ratio(aspect_ratio: str) -> bool:
    """
    Check if an aspect ratio string is valid.
    
    Args:
        aspect_ratio: Aspect ratio string to validate
    
    Returns:
        True if the ratio is valid, False otherwise.
    """
    return aspect_ratio in VALID_ASPECT_RATIOS


def normalize_aspect_ratio(aspect_ratio: str) -> str:
    """
    Normalize an aspect ratio string.
    
    Args:
        aspect_ratio: Aspect ratio string to normalize
    
    Returns:
        Normalized aspect ratio string.
        Returns default (16:9) if the input is invalid.
    """
    if not is_valid_aspect_ratio(aspect_ratio):
        return DEFAULT_ASPECT_RATIO
    return aspect_ratio


def list_aspect_ratios() -> list[dict]:
    """
    Get a list of all available aspect ratios for API responses.
    
    Returns:
        List of dictionaries with aspect ratio information.
    """
    return [
        {
            "aspect_ratio": config.aspect_ratio,
            "width": config.width,
            "height": config.height,
            "label": config.label,
            "description": config.description,
        }
        for config in ASPECT_RATIO_CONFIGS.values()
    ]
