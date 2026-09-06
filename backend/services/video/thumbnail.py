"""
backend/services/video/thumbnail.py — YouTube thumbnail generator.

Creates 1280×720 JPG thumbnails using Pillow only.
No AI image generation. No external APIs.

Provides 3 layout templates chosen automatically by title hash.
"""

from __future__ import annotations

import hashlib
import logging
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
