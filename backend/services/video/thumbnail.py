"""
backend/services/video/thumbnail.py — YouTube thumbnail generator.

Creates 1280×720 JPG thumbnails using Pillow only.
No AI image generation. No external APIs.

Provides 3 layout templates chosen automatically by title hash.

Phase 3J: Added representative scene frame extraction with fallback chain.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Thumbnail palettes (bg_top, bg_bottom, title_col, accent) ────────────────
_THUMB_PALETTES = [
    ((10, 20, 50),  (30, 60, 120),  (255, 255, 255), (255, 200, 50)),
    ((40, 10, 50),  (90, 25, 100),  (255, 255, 255), (255, 120, 200)),
    ((10, 45, 30),  (20, 90, 60),   (255, 255, 255), (100, 255, 150)),
]


def _pick_thumb_palette(seed: str) -> tuple:
    idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(_THUMB_PALETTES)
    return _THUMB_PALETTES[idx]


def _gradient(width: int, height: int, top: tuple, bottom: tuple):
    from PIL import Image
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        t = y / max(height - 1, 1)
        r = int(top[0] + t * (bottom[0] - top[0]))
        g = int(top[1] + t * (bottom[1] - top[1]))
        b = int(top[2] + t * (bottom[2] - top[2]))
        for x in range(width):
            px[x, y] = (r, g, b)
    return img


def _load_fonts(big: int, med: int, sm: int):
    from PIL import ImageFont
    for font_path in (
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ):
        if Path(font_path).exists():
            return (
                ImageFont.truetype(font_path, big),
                ImageFont.truetype(font_path, med),
                ImageFont.truetype(font_path, sm),
            )
    return (
        ImageFont.load_default(size=big),
        ImageFont.load_default(size=med),
        ImageFont.load_default(size=sm),
    )


def _layout_centered(draw, img, title: str, hook: str, palette: tuple, W: int, H: int):
    """Template 1: Centered large title + hook below."""
    from PIL import ImageDraw
    bg_top, bg_bottom, title_col, accent = palette
    font_big, font_med, font_sm = _load_fonts(90, 48, 32)

    # Accent bars top/bottom
    draw.rectangle([0, 0, W, 12], fill=accent)
    draw.rectangle([0, H - 12, W, H], fill=accent)

    # Title
    title_lines = textwrap.wrap(title[:60], width=22)[:2]
    draw.multiline_text(
        (W // 2, H // 2 - 60),
        "\n".join(title_lines),
        fill=title_col,
        font=font_big,
        anchor="mm",
        align="center",
        spacing=10,
    )

    # Hook
    if hook:
        hook_lines = textwrap.wrap(hook[:120], width=45)[:2]
        draw.multiline_text(
            (W // 2, H // 2 + 100),
            "\n".join(hook_lines),
            fill=(*accent, 230),
            font=font_med,
            anchor="mm",
            align="center",
            spacing=8,
        )


def _layout_left_bold(draw, img, title: str, hook: str, palette: tuple, W: int, H: int):
    """Template 2: Bold left-aligned title with right accent stripe."""
    from PIL import ImageDraw
    bg_top, bg_bottom, title_col, accent = palette
    font_big, font_med, font_sm = _load_fonts(80, 44, 30)

    # Right accent stripe
    draw.rectangle([W - 20, 0, W, H], fill=accent)

    # Big title, left-aligned, vertically centered
    title_lines = textwrap.wrap(title[:60], width=25)[:3]
    title_y = H // 2 - len(title_lines) * 50
    draw.multiline_text(
        (80, title_y),
        "\n".join(title_lines),
        fill=title_col,
        font=font_big,
        spacing=12,
    )

    if hook:
        hook_lines = textwrap.wrap(hook[:130], width=50)[:2]
        draw.multiline_text(
            (80, title_y + len(title_lines) * 95 + 20),
            "\n".join(hook_lines),
            fill=(*accent, 210),
            font=font_med,
            spacing=8,
        )


def _layout_question(draw, img, title: str, hook: str, palette: tuple, W: int, H: int):
    """Template 3: Question-mark style with large emoji-like indicator."""
    from PIL import ImageDraw
    bg_top, bg_bottom, title_col, accent = palette
    font_big, font_med, font_sm = _load_fonts(85, 46, 30)

    # Diagonal accent band (approximated by polygon)
    draw.polygon(
        [(0, H - 80), (W // 3, H - 80), (W // 3 + 40, H), (0, H)],
        fill=accent,
    )

    # Title
    title_lines = textwrap.wrap(title[:60], width=24)[:2]
    draw.multiline_text(
        (W // 2, 100),
        "\n".join(title_lines),
        fill=title_col,
        font=font_big,
        anchor="ma",
        align="center",
        spacing=12,
    )

    # Hook in accent colour, lower half
    if hook:
        hook_lines = textwrap.wrap(hook[:120], width=48)[:2]
        draw.multiline_text(
            (W // 2, H // 2 + 80),
            "\n".join(hook_lines),
            fill=(*accent, 230),
            font=font_med,
            anchor="ma",
            align="center",
            spacing=8,
        )


_LAYOUTS = [_layout_centered, _layout_left_bold, _layout_question]


# ── Phase 3J: Frame extraction ──────────────────────────────────────────────────

def _get_ffmpeg_path() -> str:
    """Get FFmpeg path from configuration."""
    from backend.services.media.ffmpeg import get_ffmpeg_path, FFmpegNotFoundError
    path = get_ffmpeg_path()
    if not Path(path).exists():
        raise FFmpegNotFoundError(
            f"FFmpeg not found at: {path}. Set FFMPEG_PATH in backend/.env."
        )
    return path


def _get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        ffmpeg_path = _get_ffmpeg_path()
        ffprobe_path = ffmpeg_path.replace("ffmpeg", "ffprobe")
        result = subprocess.run(
            [ffprobe_path, "-v", "quiet", "-print_format", "json",
             "-show_streams", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("ffprobe failed for %s: %s", video_path, result.stderr)
            return 0.0
        data = __import__("json").loads(result.stdout)
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                duration = float(stream.get("duration", 0))
                if duration > 0:
                    return duration
        return 0.0
    except Exception as e:
        logger.warning("Failed to get video duration for %s: %s", video_path, e)
        return 0.0


def _validate_extracted_frame(frame_path: Path) -> bool:
    """Validate that an extracted frame is a valid, non-corrupt image."""
    if not frame_path.exists():
        return False
    if frame_path.stat().st_size == 0:
        logger.warning("Extracted frame is zero bytes: %s", frame_path)
        return False
    try:
        from PIL import Image
        img = Image.open(frame_path)
        img.verify()  # Verify it's a valid image
        img.close()  # Close file after verify
        # Reopen for size check (verify closes the file)
        img = Image.open(frame_path)
        width, height = img.size
        if width < 100 or height < 100:
            logger.warning("Extracted frame too small: %dx%d", width, height)
            img.close()
            return False
        # Check if image is mostly black/blank
        # Use get_flattened_data to avoid deprecation warning
        try:
            pixels = list(img.get_flattened_data())
        except AttributeError:
            pixels = list(img.getdata())
        img.close()  # Close before processing pixels
        if len(pixels) > 0:
            # Sample a few pixels to check for blank/black frames
            sample_pixels = pixels[::len(pixels)//100] if len(pixels) > 100 else pixels
            avg_brightness = sum(sum(p[:3]) for p in sample_pixels) / len(sample_pixels) / 3
            if avg_brightness < 10:  # Very dark frame
                logger.warning("Extracted frame appears too dark (avg brightness: %.1f)", avg_brightness)
                return False
        return True
    except Exception as e:
        logger.warning("Frame validation failed for %s: %s", frame_path, e)
        return False


def _extract_frame(video_path: Path, seek_seconds: float, output_path: Path) -> bool:
    """
    Extract a single frame from video at seek_seconds using FFmpeg.
    
    Returns True if extraction succeeded and produced a valid frame, False otherwise.
    """
    try:
        ffmpeg_path = _get_ffmpeg_path()
        cmd = [
            ffmpeg_path,
            "-y",  # Overwrite output
            "-ss", str(seek_seconds),
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",  # High quality JPEG
            str(output_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("FFmpeg frame extraction failed: %s", result.stderr)
            return False
        
        # Validate the extracted frame
        if _validate_extracted_frame(output_path):
            logger.info("Successfully extracted frame at %.2fs from %s", seek_seconds, video_path.name)
            return True
        else:
            logger.warning("Extracted frame validation failed at %.2fs", seek_seconds)
            return False
    except Exception as e:
        logger.warning("Frame extraction exception: %s", e)
        return False


def generate_thumbnail_from_frame(
    video_path: Path,
    title: str,
    hook: str,
    output_path: Path,
    style: str = "text_only",
    width: int = 1280,
    height: int = 720,
) -> tuple[Path, list[str]]:
    """
    Generate thumbnail with optional representative scene frame extraction.
    
    Args:
        video_path: Path to the finished MP4 video.
        title: Video title.
        hook: Opening hook line.
        output_path: Where to write the thumbnail.
        style: Thumbnail style - "text_only", "scene_frame", or "scene_frame_overlay".
        width: Thumbnail width (default 1280).
        height: Thumbnail height (default 720).
    
    Returns:
        Tuple of (output_path, fallback_actions) where fallback_actions lists any
        fallbacks that were used during generation.
    """
    from PIL import Image, ImageDraw, ImageOps
    
    fallback_actions = []
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Resolve NULL/default style to THUMBNAIL_DEFAULT_STYLE
    if not style or style.strip() == "":
        style = os.getenv("THUMBNAIL_DEFAULT_STYLE", "text_only").strip()
        if not style:
            style = "text_only"
    
    # For text_only, delegate to existing function unchanged
    if style == "text_only":
        logger.info("Using text_only thumbnail style (delegating to existing generator)")
        generate_thumbnail(title=title, hook=hook, output_path=output_path, width=width, height=height)
        return output_path, fallback_actions
    
    # For scene_frame and scene_frame_overlay, extract frame
    if style in ("scene_frame", "scene_frame_overlay"):
        if not video_path.exists():
            logger.warning("Video file not found for frame extraction: %s", video_path)
            fallback_actions.append("Video file not found; falling back to text_only")
            generate_thumbnail(title=title, hook=hook, output_path=output_path, width=width, height=height)
            return output_path, fallback_actions
        
        # Get video duration
        duration = _get_video_duration(video_path)
        if duration <= 0:
            logger.warning("Could not determine video duration for %s", video_path)
            fallback_actions.append("Could not determine video duration; falling back to text_only")
            generate_thumbnail(title=title, hook=hook, output_path=output_path, width=width, height=height)
            return output_path, fallback_actions
        
        # Try multiple seek points with fallback
        frame_seek_pct = float(os.getenv("THUMBNAIL_FRAME_SEEK_PCT", "0.20"))
        seek_percentages = [frame_seek_pct, 0.35, 0.50]  # Configured, then fallbacks
        
        temp_frame_path = output_path.parent / f"temp_frame_{output_path.stem}.jpg"
        extracted_frame = None
        
        for seek_pct in seek_percentages:
            seek_seconds = duration * seek_pct
            logger.info("Attempting frame extraction at %.1f%% (%.2fs)", seek_pct * 100, seek_seconds)
            
            if _extract_frame(video_path, seek_seconds, temp_frame_path):
                extracted_frame = temp_frame_path
                break
            else:
                logger.info("Frame extraction failed at %.1f%%, trying next fallback", seek_pct * 100)
        
        if not extracted_frame:
            logger.warning("All frame extraction attempts failed, falling back to text_only")
            fallback_actions.append("All frame extraction attempts failed; falling back to text_only")
            generate_thumbnail(title=title, hook=hook, output_path=output_path, width=width, height=height)
            # Clean up temp file if it exists
            if temp_frame_path.exists():
                temp_frame_path.unlink()
            return output_path, fallback_actions
        
        try:
            # Load and process the extracted frame
            frame_img = Image.open(extracted_frame).convert("RGB")
            
            # Resize/crop to target dimensions (center-crop for aspect ratio conversion)
            frame_img = ImageOps.fit(frame_img, (width, height), Image.Resampling.LANCZOS)
            extracted_frame.unlink()  # Clean up temp frame file early
            
            if style == "scene_frame":
                # Use frame as full background with gradient overlay for text readability
                # Create a semi-transparent gradient overlay at the bottom
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay, "RGBA")
                
                # Gradient from transparent at top to semi-transparent black at bottom
                for y in range(height):
                    alpha = int(180 * (y / height))  # 0 to 180 alpha
                    if y > height * 0.6:  # Only gradient the bottom 40%
                        draw.rectangle([(0, y), (width, y + 1)], fill=(0, 0, 0, alpha))
                
                # Composite gradient over frame
                frame_img = Image.alpha_composite(frame_img.convert("RGBA"), overlay).convert("RGB")
                
                # Now overlay text using existing layout functions
                palette = _pick_thumb_palette(title)
                draw = ImageDraw.Draw(frame_img, "RGBA")
                layout_idx = int(hashlib.md5(title.encode()).hexdigest(), 16) % len(_LAYOUTS)
                _LAYOUTS[layout_idx](draw, frame_img, title, hook, palette, width, height)
                
            elif style == "scene_frame_overlay":
                # Blend frame with gradient background using readability treatment
                palette = _pick_thumb_palette(title)
                bg_top, bg_bottom, _, _ = palette
                
                # Create gradient background
                gradient_bg = _gradient(width, height, bg_top, bg_bottom)
                
                # Blend frame with gradient (30% frame, 70% gradient for readability)
                blended = Image.blend(frame_img, gradient_bg, 0.7)
                
                # Add subtle dark overlay at bottom for text readability
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                draw_overlay = ImageDraw.Draw(overlay, "RGBA")
                for y in range(height):
                    if y > height * 0.5:  # Darken bottom 50%
                        alpha = int(100 * ((y - height * 0.5) / (height * 0.5)))
                        draw_overlay.rectangle([(0, y), (width, y + 1)], fill=(0, 0, 0, alpha))
                
                blended = Image.alpha_composite(blended.convert("RGBA"), overlay).convert("RGB")
                
                # Overlay text
                draw = ImageDraw.Draw(blended, "RGBA")
                layout_idx = int(hashlib.md5(title.encode()).hexdigest(), 16) % len(_LAYOUTS)
                _LAYOUTS[layout_idx](draw, blended, title, hook, palette, width, height)
                frame_img = blended
            
            # Save final thumbnail
            frame_img.save(str(output_path), "JPEG", quality=92, optimize=True)
            logger.info("Thumbnail written with %s style: %s (%dx%d)", style, output_path, width, height)
            
            return output_path, fallback_actions
            
        except Exception as e:
            logger.warning("Frame processing failed: %s, falling back to text_only", e)
            fallback_actions.append(f"Frame processing failed ({str(e)}); falling back to text_only")
            generate_thumbnail(title=title, hook=hook, output_path=output_path, width=width, height=height)
            return output_path, fallback_actions
        finally:
            # Clean up temp frame file
            if temp_frame_path.exists():
                temp_frame_path.unlink()
    
    # Unknown style - fall back to text_only
    logger.warning("Unknown thumbnail style '%s', falling back to text_only", style)
    fallback_actions.append(f"Unknown thumbnail style '{style}'; falling back to text_only")
    generate_thumbnail(title=title, hook=hook, output_path=output_path, width=width, height=height)
    return output_path, fallback_actions


def generate_thumbnail(
    title: str,
    hook: str,
    output_path: str | Path,
    width: int = 1280,
    height: int = 720,
) -> Path:
    """
    Generate a YouTube thumbnail JPG using Pillow.

    Args:
        title:        Video title.
        hook:         Opening hook line (used as subtitle text).
        output_path:  Where to write the JPG.
        width, height: Thumbnail dimensions (default 1280×720).

    Returns:
        Path to written JPG.
    """
    from PIL import Image, ImageDraw

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    palette = _pick_thumb_palette(title)
    bg_top, bg_bottom, _, _ = palette

    img = _gradient(width, height, bg_top, bg_bottom)
    draw = ImageDraw.Draw(img, "RGBA")

    # Choose layout by title hash
    layout_idx = int(hashlib.md5(title.encode()).hexdigest(), 16) % len(_LAYOUTS)
    _LAYOUTS[layout_idx](draw, img, title, hook, palette, width, height)

    # Convert back to RGB for JPEG (JPEG doesn't support alpha)
    img_rgb = img.convert("RGB")
    img_rgb.save(str(out), "JPEG", quality=92, optimize=True)
    logger.info("Thumbnail written: %s (%dx%d)", out, width, height)
    return out
