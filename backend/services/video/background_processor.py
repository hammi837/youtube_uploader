"""
Background visual processing for video generation (Phase 3E.3).

Supports image backgrounds with Pillow-based compositing.
Video background support deferred to follow-up phase.
"""

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw
from backend.services.video.background_config import (
    BackgroundConfig,
    BackgroundType,
    BackgroundFit,
    get_background_config,
    get_default_background_config,
)


def apply_image_background(
    scene_card_path: Path,
    background_config: BackgroundConfig,
    width: int,
    height: int,
) -> Path:
    """
    Apply an image background to a scene card.

    Args:
        scene_card_path: Path to the generated scene card PNG
        background_config: Background configuration
        width: Target width
        height: Target height

    Returns:
        Path to the composite image with background.
        Returns original path if background cannot be applied.
    """
    if background_config.background_type != BackgroundType.LOCAL_IMAGE:
        return scene_card_path

    if not background_config.background_path:
        return scene_card_path

    bg_path = Path(background_config.background_path)
    if not bg_path.exists():
        return scene_card_path

    try:
        # Load scene card and background
        scene_card = Image.open(scene_card_path).convert("RGBA")
        background = Image.open(bg_path).convert("RGBA")

        # Resize background to target dimensions based on fit mode
        if background_config.background_fit == BackgroundFit.COVER:
            # Crop to fill (may clip edges)
            bg_resized = Image.new("RGBA", (width, height), (0, 0, 0, 255))
            # Calculate scaling
            bg_ratio = background.width / background.height
            target_ratio = width / height
            
            if bg_ratio > target_ratio:
                # Background is wider than target
                new_height = height
                new_width = int(height * bg_ratio)
            else:
                # Background is taller than target
                new_width = width
                new_height = int(width / bg_ratio)
            
            bg_scaled = background.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Center crop
            left = (new_width - width) // 2
            top = (new_height - height) // 2
            bg_resized.paste(bg_scaled, (-left, -top))
            
        elif background_config.background_fit == BackgroundFit.CONTAIN:
            # Fit within bounds (may have letterbox)
            bg_ratio = background.width / background.height
            target_ratio = width / height
            
            if bg_ratio > target_ratio:
                # Background is wider than target
                new_width = width
                new_height = int(width / bg_ratio)
            else:
                # Background is taller than target
                new_height = height
                new_width = int(height * bg_ratio)
            
            bg_scaled = background.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Center placement
            bg_resized = Image.new("RGBA", (width, height), (0, 0, 0, 255))
            left = (width - new_width) // 2
            top = (height - new_height) // 2
            bg_resized.paste(bg_scaled, (left, top))
            
        else:  # FILL or STRETCH
            # Stretch to fill (may distort)
            bg_resized = background.resize((width, height), Image.Resampling.LANCZOS)

        # Apply background first
        composite = bg_resized.copy()

        # Apply scene card on top
        composite.paste(scene_card, (0, 0), scene_card)

        # Apply dark overlay for text readability
        if background_config.overlay_opacity > 0:
            overlay = Image.new("RGBA", (width, height), (0, 0, 0, int(255 * background_config.overlay_opacity)))
            composite = Image.alpha_composite(composite, overlay)

        # Save composite
        output_path = scene_card_path.parent / f"{scene_card_path.stem}_with_bg{scene_card_path.suffix}"
        composite.save(str(output_path), "PNG")

        return output_path

    except Exception as e:
        # Fall back to original scene card if background processing fails
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to apply image background: {e}, using original scene card")
        return scene_card_path


def apply_solid_color_background(
    scene_card_path: Path,
    background_config: BackgroundConfig,
    width: int,
    height: int,
) -> Path:
    """
    Apply a solid color background to a scene card.

    Args:
        scene_card_path: Path to the generated scene card PNG
        background_config: Background configuration
        width: Target width
        height: Target height

    Returns:
        Path to the composite image with background.
        Returns original path if background cannot be applied.
    """
    if background_config.background_type != BackgroundType.SOLID_COLOR:
        return scene_card_path

    if not background_config.background_color:
        return scene_card_path

    try:
        # Load scene card
        scene_card = Image.open(scene_card_path).convert("RGBA")

        # Create solid color background
        background = Image.new("RGBA", (width, height), background_config.background_color)

        # Apply background first
        composite = background.copy()

        # Apply scene card on top
        composite.paste(scene_card, (0, 0), scene_card)

        # Apply dark overlay for text readability
        if background_config.overlay_opacity > 0:
            overlay = Image.new("RGBA", (width, height), (0, 0, 0, int(255 * background_config.overlay_opacity)))
            composite = Image.alpha_composite(composite, overlay)

        # Save composite
        output_path = scene_card_path.parent / f"{scene_card_path.stem}_with_bg{scene_card_path.suffix}"
        composite.save(str(output_path), "PNG")

        return output_path

    except Exception as e:
        # Fall back to original scene card if background processing fails
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to apply solid color background: {e}, using original scene card")
        return scene_card_path


def apply_background_to_scene(
    scene_card_path: Path,
    background_config: BackgroundConfig,
    width: int,
    height: int,
) -> Path:
    """
    Apply background to a scene card based on configuration.

    Args:
        scene_card_path: Path to the generated scene card PNG
        background_config: Background configuration
        width: Target width
        height: Target height

    Returns:
        Path to the composite image with background.
        Returns original path if background cannot be applied.
    """
    if background_config.background_type == BackgroundType.LOCAL_IMAGE:
        return apply_image_background(scene_card_path, background_config, width, height)
    elif background_config.background_type == BackgroundType.SOLID_COLOR:
        return apply_solid_color_background(scene_card_path, background_config, width, height)
    else:
        # For gradient, placeholder, or local_video (deferred), use original
        return scene_card_path
