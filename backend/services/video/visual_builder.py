"""
backend/services/video/visual_builder.py — Pillow-based scene card generator.

Generates a 1920×1080 PNG for each scene using only Pillow.
No AI image generation, no external image APIs, no downloads.

Each card shows:
  - gradient background
  - scene title (large)
  - key point / narration excerpt (medium)
  - subtle scene number indicator

The visual style is intentionally minimal and legible.
"""

from __future__ import annotations

import hashlib
import logging
import textwrap
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Colour palettes ───────────────────────────────────────────────────────────
# Each palette is (bg_top, bg_bottom, title_colour, body_colour, accent)
_PALETTES = [
    # Deep blue
    ((15, 25, 60),  (25, 50, 110),  (255, 255, 255), (200, 220, 255), (80, 160, 255)),
    # Dark teal
    ((10, 40, 45),  (20, 75, 80),   (255, 255, 255), (180, 230, 235), (60, 200, 210)),
    # Deep purple
    ((35, 15, 60),  (65, 30, 100),  (255, 255, 255), (220, 200, 255), (160, 100, 255)),
    # Dark green
    ((15, 40, 25),  (25, 75, 45),   (255, 255, 255), (190, 240, 205), (60, 200, 100)),
    # Dark maroon
    ((55, 15, 20),  (100, 25, 35),  (255, 255, 255), (255, 210, 215), (255, 100, 110)),
    # Slate
    ((30, 35, 45),  (55, 65, 80),   (255, 255, 255), (215, 220, 230), (130, 180, 230)),
]


def _pick_palette(seed: str) -> tuple:
    idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(_PALETTES)
    return _PALETTES[idx]


def _load_fonts(size_large: int, size_medium: int, size_small: int):
    """
    Load fonts with graceful fallback to default.
    Returns (font_large, font_medium, font_small).
    """
    try:
        from PIL import ImageFont
        # Try Windows system fonts
        for font_name in (
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "C:/Windows/Fonts/verdana.ttf",
        ):
            if Path(font_name).exists():
                return (
                    ImageFont.truetype(font_name, size_large),
                    ImageFont.truetype(font_name, size_medium),
                    ImageFont.truetype(font_name, size_small),
                )
        # PIL default bitmap font
        return (
            ImageFont.load_default(size=size_large),
            ImageFont.load_default(size=size_medium),
            ImageFont.load_default(size=size_small),
        )
    except Exception:
        from PIL import ImageFont
        return (
            ImageFont.load_default(),
            ImageFont.load_default(),
            ImageFont.load_default(),
        )


def _make_gradient(width: int, height: int, top: tuple, bottom: tuple):
    """Create a vertical gradient as a PIL Image."""
    from PIL import Image
    img = Image.new("RGB", (width, height))
    pixels = img.load()
    for y in range(height):
        t = y / (height - 1)
        r = int(top[0] + t * (bottom[0] - top[0]))
        g = int(top[1] + t * (bottom[1] - top[1]))
        b = int(top[2] + t * (bottom[2] - top[2]))
        for x in range(width):
            pixels[x, y] = (r, g, b)
    return img


def generate_scene_card(
    scene_number: int,
    title: str,
    narration: str,
    visual_description: str,
    output_path: str | Path,
    width: int = 1920,
    height: int = 1080,
    topic_seed: str = "",
) -> Path:
    """
    Generate a scene card PNG using Pillow only.

    Args:
        scene_number:       1-based scene index.
        title:              Video/script title (shown at top).
        narration:          Scene narration text (excerpt shown).
        visual_description: Scene visual description (optional hint text).
        output_path:        Where to write the PNG.
        width, height:      Image dimensions.
        topic_seed:         Used to pick a consistent colour palette.

    Returns:
        Path to the written PNG file.
    """
    from PIL import Image, ImageDraw

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    palette = _pick_palette(topic_seed or title)
    bg_top, bg_bottom, title_col, body_col, accent_col = palette

    img = _make_gradient(width, height, bg_top, bg_bottom)
    draw = ImageDraw.Draw(img)

    # ── Fonts ──────────────────────────────────────────────────────────────
    font_title  = None
    font_body   = None
    font_small  = None
    font_scene  = None

    try:
        font_title, font_body, font_small = _load_fonts(72, 40, 28)
        _, _, font_scene = _load_fonts(72, 40, 24)
    except Exception:
        pass  # will use None → Pillow default

    # ── Accent bar (left edge) ─────────────────────────────────────────────
    bar_w = 12
    draw.rectangle([0, 0, bar_w, height], fill=accent_col)

    # ── Scene number pill ──────────────────────────────────────────────────
    pill_text = f"SCENE {scene_number:02d}"
    pill_x, pill_y = 60, 60
    pill_pad = 16
    try:
        bbox = draw.textbbox((pill_x, pill_y), pill_text, font=font_small)
        pill_w = bbox[2] - bbox[0] + pill_pad * 2
        pill_h = bbox[3] - bbox[1] + pill_pad
    except Exception:
        pill_w, pill_h = 160, 40

    draw.rounded_rectangle(
        [pill_x - pill_pad, pill_y - pill_pad // 2,
         pill_x + pill_w,   pill_y + pill_h],
        radius=8,
        fill=(*accent_col, 200),
    )
    draw.text((pill_x, pill_y), pill_text, fill=(255, 255, 255), font=font_small)

    # ── Horizontal divider ─────────────────────────────────────────────────
    div_y = 160
    draw.line([(60, div_y), (width - 60, div_y)], fill=(*accent_col, 120), width=2)

    # ── Title (script/video title, truncated) ──────────────────────────────
    title_text = title[:70] + ("…" if len(title) > 70 else "")
    draw.text((60, 185), title_text, fill=(*title_col, 220), font=font_title)

    # ── Narration excerpt ──────────────────────────────────────────────────
    # Wrap narration to ~80 chars per line, show first 6 lines
    excerpt = narration.strip()
    if len(excerpt) > 500:
        excerpt = excerpt[:497] + "…"
    lines = textwrap.wrap(excerpt, width=85)[:6]
    narration_text = "\n".join(lines)

    body_y = 320
    draw.multiline_text(
        (60, body_y),
        narration_text,
        fill=body_col,
        font=font_body,
        spacing=12,
    )

    # ── Visual hint (smaller, bottom area) ────────────────────────────────
    if visual_description and visual_description.strip():
        hint = f"🎥  {visual_description[:120]}"
        draw.text(
            (60, height - 100),
            hint,
            fill=(*accent_col, 160),
            font=font_small,
        )

    # ── Bottom divider ─────────────────────────────────────────────────────
    draw.line([(60, height - 130), (width - 60, height - 130)],
              fill=(*accent_col, 80), width=1)

    img.save(str(out), "PNG", optimize=False)
    logger.debug("Scene card written: %s", out)
    return out


def generate_title_card(
    title: str,
    hook: str,
    output_path: str | Path,
    width: int = 1920,
    height: int = 1080,
    topic_seed: str = "",
) -> Path:
    """Generate an opening title card."""
    from PIL import Image, ImageDraw

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    palette = _pick_palette(topic_seed or title)
    bg_top, bg_bottom, title_col, body_col, accent_col = palette

    img = _make_gradient(width, height, bg_top, bg_bottom)
    draw = ImageDraw.Draw(img)

    font_title, font_body, font_small = _load_fonts(88, 44, 28)

    # Accent bar
    draw.rectangle([0, 0, 12, height], fill=accent_col)

    # Central title
    title_lines = textwrap.wrap(title, width=40)[:3]
    title_text = "\n".join(title_lines)

    # Estimate vertical centering
    line_h = 100
    total_h = len(title_lines) * line_h
    title_y = (height - total_h) // 2 - 60

    draw.multiline_text(
        (width // 2, title_y),
        title_text,
        fill=title_col,
        font=font_title,
        anchor="ma",
        align="center",
        spacing=16,
    )

    # Hook below title
    if hook:
        hook_text = hook.strip()[:200]
        hook_lines = textwrap.wrap(hook_text, width=70)[:3]
        hook_str = "\n".join(hook_lines)
        draw.multiline_text(
            (width // 2, title_y + total_h + 50),
            hook_str,
            fill=body_col,
            font=font_body,
            anchor="ma",
            align="center",
            spacing=10,
        )

    img.save(str(out), "PNG", optimize=False)
    logger.debug("Title card written: %s", out)
    return out
