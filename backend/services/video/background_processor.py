"""
Background visual processing for video generation (Phase 3E.3 / P0.1).

Composites RGBA text-overlay scene cards onto image / solid / gradient backgrounds.
Full-frame darkening overlays are NOT applied — readability comes from dark-glass
panels drawn on the scene card itself.
"""

from pathlib import Path
from typing import Optional

from PIL import Image
from backend.services.video.background_config import (
    BackgroundConfig,
    BackgroundType,
    BackgroundFit,
)


def _composite_overlay_on_background(
    scene_card_path: Path,
    background: Image.Image,
    width: int,
    height: int,
) -> Path:
    """
    Paste an RGBA scene overlay onto an opaque background and save as PNG.
    Returns path to composited image (*_with_bg.png).
    """
    scene_card = Image.open(scene_card_path).convert("RGBA")
    if background.size != (width, height):
        background = background.resize((width, height), Image.Resampling.LANCZOS)
    if background.mode != "RGBA":
        background = background.convert("RGBA")

    composite = background.copy()
    composite.paste(scene_card, (0, 0), scene_card)

    # Ken Burns / FFmpeg image inputs work best as opaque RGB frames
    output = composite.convert("RGB")
    output_path = scene_card_path.parent / f"{scene_card_path.stem}_with_bg.png"
    output.save(str(output_path), "PNG")
    return output_path


def apply_image_background(
    scene_card_path: Path,
    background_config: BackgroundConfig,
    width: int,
    height: int,
) -> Path:
    """Apply a local image background under an RGBA scene overlay."""
    if background_config.background_type != BackgroundType.LOCAL_IMAGE:
        return scene_card_path

    if not background_config.background_path:
        return scene_card_path

    bg_path = Path(background_config.background_path)
    if not bg_path.exists():
        return scene_card_path

    try:
        background = Image.open(bg_path).convert("RGBA")

        if background_config.background_fit == BackgroundFit.COVER:
            bg_resized = Image.new("RGBA", (width, height), (0, 0, 0, 255))
            bg_ratio = background.width / max(background.height, 1)
            target_ratio = width / max(height, 1)

            if bg_ratio > target_ratio:
                new_height = height
                new_width = int(height * bg_ratio)
            else:
                new_width = width
                new_height = int(width / bg_ratio)

            bg_scaled = background.resize((new_width, new_height), Image.Resampling.LANCZOS)
            left = (new_width - width) // 2
            top = (new_height - height) // 2
            bg_resized.paste(bg_scaled, (-left, -top))

        elif background_config.background_fit == BackgroundFit.CONTAIN:
            bg_ratio = background.width / max(background.height, 1)
            target_ratio = width / max(height, 1)

            if bg_ratio > target_ratio:
                new_width = width
                new_height = int(width / bg_ratio)
            else:
                new_height = height
                new_width = int(height * bg_ratio)

            bg_scaled = background.resize((new_width, new_height), Image.Resampling.LANCZOS)
            bg_resized = Image.new("RGBA", (width, height), (0, 0, 0, 255))
            left = (width - new_width) // 2
            top = (height - new_height) // 2
            bg_resized.paste(bg_scaled, (left, top))

        else:  # FILL / STRETCH
            bg_resized = background.resize((width, height), Image.Resampling.LANCZOS)

        return _composite_overlay_on_background(scene_card_path, bg_resized, width, height)

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to apply image background: %s, using original scene card", e
        )
        return scene_card_path


def apply_solid_color_background(
    scene_card_path: Path,
    background_config: BackgroundConfig,
    width: int,
    height: int,
) -> Path:
    """Apply a solid color background under an RGBA scene overlay."""
    if background_config.background_type != BackgroundType.SOLID_COLOR:
        return scene_card_path

    if not background_config.background_color:
        return scene_card_path

    try:
        background = Image.new("RGBA", (width, height), background_config.background_color)
        return _composite_overlay_on_background(scene_card_path, background, width, height)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to apply solid color background: %s, using original scene card", e
        )
        return scene_card_path


def apply_gradient_background(
    scene_card_path: Path,
    width: int,
    height: int,
    topic_seed: str = "",
) -> Path:
    """Bake a palette gradient under an RGBA overlay (fallback / default mode)."""
    try:
        from backend.services.video.visual_builder import pick_gradient_palette, _make_gradient
        bg_top, bg_bottom, *_rest = pick_gradient_palette(topic_seed or scene_card_path.stem)
        gradient = _make_gradient(width, height, bg_top, bg_bottom).convert("RGBA")
        return _composite_overlay_on_background(scene_card_path, gradient, width, height)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to apply gradient background: %s, using original scene card", e
        )
        return scene_card_path


def apply_background_to_scene(
    scene_card_path: Path,
    background_config: BackgroundConfig,
    width: int,
    height: int,
    topic_seed: str = "",
) -> Path:
    """
    Apply background under a scene overlay based on configuration.

    Gradient / placeholder / unknown types bake a gradient so transparent
    overlays remain valid Ken Burns frames.
    """
    if background_config.background_type == BackgroundType.LOCAL_IMAGE:
        return apply_image_background(scene_card_path, background_config, width, height)
    if background_config.background_type == BackgroundType.SOLID_COLOR:
        return apply_solid_color_background(scene_card_path, background_config, width, height)
    if background_config.background_type == BackgroundType.LOCAL_VIDEO:
        # Video is composited later in FFmpeg; keep transparent overlay PNG.
        return scene_card_path
    # gradient, placeholder, visual_provider (pre-resolved), etc.
    return apply_gradient_background(scene_card_path, width, height, topic_seed=topic_seed)
