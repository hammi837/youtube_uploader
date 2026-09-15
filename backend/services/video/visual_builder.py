"""
backend/services/video/visual_builder.py — Pillow-based scene card generator.

Generates a 1920×1080 PNG for each scene using only Pillow.
No AI image generation, no external image APIs, no downloads.

Phase 3E.1: Template-aware rendering with minimal_dark and quote_fact templates.

Templates:
  - minimal_dark: Current style (gradient + text)
  - quote_fact: Large quote emphasis with bold typography
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
    template: Optional[object] = None,  # Phase 3E.1: TemplateConfig
    aspect_ratio: str = "16:9",  # Phase 3E.2
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
        template:           TemplateConfig for styling (Phase 3E.1).
        aspect_ratio:       Aspect ratio for layout adjustments (Phase 3E.2).

    Returns:
        Path to the written PNG file.
    """
    # Phase 3E.1: Get template, default to minimal_dark
    if template is None:
        from backend.services.video.templates import get_default_template
        template = get_default_template()

    # Phase 3E.2: Get aspect ratio config for layout adjustments
    from backend.services.video.aspect_ratio import get_aspect_ratio_config
    ar_config = get_aspect_ratio_config(aspect_ratio)

    # Route to template-specific rendering
    if template.template_id == "quote_fact":
        return _generate_quote_fact_scene_card(
            scene_number, title, narration, visual_description,
            output_path, width, height, topic_seed, template, aspect_ratio
        )
    else:  # minimal_dark and future templates
        return _generate_minimal_dark_scene_card(
            scene_number, title, narration, visual_description,
            output_path, width, height, topic_seed, template, aspect_ratio
        )


def generate_title_card(
    title: str,
    hook: str,
    output_path: str | Path,
    width: int = 1920,
    height: int = 1080,
    topic_seed: str = "",
    template: Optional[object] = None,  # Phase 3E.1: TemplateConfig
    aspect_ratio: str = "16:9",  # Phase 3E.2
) -> Path:
    """Generate an opening title card with aspect ratio support."""
    # Phase 3E.1: Get template, default to minimal_dark
    if template is None:
        from backend.services.video.templates import get_default_template
        template = get_default_template()

    # Route to template-specific rendering
    if template.template_id == "quote_fact":
        return _generate_quote_fact_title_card(
            title, hook, output_path, width, height, topic_seed, template, aspect_ratio
        )
    else:  # minimal_dark and future templates
        return _generate_minimal_dark_title_card(
            title, hook, output_path, width, height, topic_seed, template, aspect_ratio
        )
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


# ── Phase 3E.1: Template-specific implementations ───────────────────────────────

def _generate_minimal_dark_title_card(
    title: str,
    hook: str,
    output_path: str | Path,
    width: int,
    height: int,
    topic_seed: str,
    template: object,
    aspect_ratio: str = "16:9",  # Phase 3E.2
) -> Path:
    """Generate minimal_dark title card with aspect ratio support."""
    from PIL import Image, ImageDraw
    from backend.services.video.aspect_ratio import get_aspect_ratio_config

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    palette = _pick_palette(topic_seed or title)
    bg_top, bg_bottom, title_col, body_col, accent_col = palette

    img = _make_gradient(width, height, bg_top, bg_bottom)
    draw = ImageDraw.Draw(img)

    # Phase 3E.2: Get aspect ratio config for layout adjustments
    ar_config = get_aspect_ratio_config(aspect_ratio)
    is_vertical = aspect_ratio == "9:16"
    
    # Adjust font sizes for vertical
    if is_vertical:
        font_title_size = 80
        font_body_size = 48
        font_small_size = 32
        text_wrap_width = 35  # Shorter lines for vertical
    else:
        font_title_size = 88
        font_body_size = 44
        font_small_size = 28
        text_wrap_width = 40

    font_title, font_body, font_small = _load_fonts(font_title_size, font_body_size, font_small_size)

    # Accent bar
    bar_w = 12 if not is_vertical else 16
    draw.rectangle([0, 0, bar_w, height], fill=accent_col)

    # Central title
    title_lines = textwrap.wrap(title, width=text_wrap_width)[:3]
    title_text = "\n".join(title_lines)

    # Estimate vertical centering
    line_h = 100 if not is_vertical else 90
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
        hook_lines = textwrap.wrap(hook_text, width=text_wrap_width * 1.75)[:3]
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
    logger.debug("Minimal dark title card written: %s", out)
    return out


def _generate_quote_fact_title_card(
    title: str,
    hook: str,
    output_path: str | Path,
    width: int,
    height: int,
    topic_seed: str,
    template: object,
    aspect_ratio: str = "16:9",  # Phase 3E.2
) -> Path:
    """Generate quote_fact title card with bold emphasis and aspect ratio support."""
    from PIL import Image, ImageDraw
    from backend.services.video.aspect_ratio import get_aspect_ratio_config

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    palette = _pick_palette(topic_seed or title)
    bg_top, bg_bottom, title_col, body_col, accent_col = palette

    img = _make_gradient(width, height, bg_top, bg_bottom)
    draw = ImageDraw.Draw(img)

    # Phase 3E.2: Get aspect ratio config for layout adjustments
    ar_config = get_aspect_ratio_config(aspect_ratio)
    is_vertical = aspect_ratio == "9:16"
    
    # Adjust font sizes for vertical
    if is_vertical:
        font_title_size = 88
        font_body_size = 56
        font_small_size = 36
        text_wrap_width = 32  # Shorter lines for vertical
    else:
        font_title_size = 96
        font_body_size = 52
        font_small_size = 32
        text_wrap_width = 36

    # Bold fonts for quote template
    font_title, font_body, font_small = _load_fonts(font_title_size, font_body_size, font_small_size)

    # Accent bar (thicker for bold style)
    bar_w = 24 if not is_vertical else 32
    draw.rectangle([0, 0, bar_w, height], fill=accent_col)

    # Central title with emphasis
    title_lines = textwrap.wrap(title, width=text_wrap_width)[:3]
    title_text = "\n".join(title_lines)

    # Estimate vertical centering
    line_h = 110 if not is_vertical else 100
    total_h = len(title_lines) * line_h
    title_y = (height - total_h) // 2 - 80

    draw.multiline_text(
        (width // 2, title_y),
        title_text,
        fill=title_col,
        font=font_title,
        anchor="ma",
        align="center",
        spacing=20,
    )

    # Hook below title with emphasis
    if hook:
        hook_text = hook.strip()[:200]
        hook_lines = textwrap.wrap(hook_text, width=text_wrap_width * 1.8)[:3]
        hook_str = "\n".join(hook_lines)
        draw.multiline_text(
            (width // 2, title_y + total_h + 60),
            hook_str,
            fill=(*accent_col, 220),
            font=font_body,
            anchor="ma",
            align="center",
            spacing=14,
        )

    img.save(str(out), "PNG", optimize=False)
    logger.debug("Quote fact title card written: %s", out)
    return out


def _generate_minimal_dark_scene_card(
    scene_number: int,
    title: str,
    narration: str,
    visual_description: str,
    output_path: str | Path,
    width: int,
    height: int,
    topic_seed: str,
    template: object,
    aspect_ratio: str = "16:9",  # Phase 3E.2
) -> Path:
    """Generate minimal_dark scene card with aspect ratio support."""
    from PIL import Image, ImageDraw
    from backend.services.video.aspect_ratio import get_aspect_ratio_config

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    palette = _pick_palette(topic_seed or title)
    bg_top, bg_bottom, title_col, body_col, accent_col = palette

    img = _make_gradient(width, height, bg_top, bg_bottom)
    draw = ImageDraw.Draw(img)

    # Phase 3E.2: Get aspect ratio config for layout adjustments
    ar_config = get_aspect_ratio_config(aspect_ratio)
    
    # Adjust layout based on aspect ratio
    is_vertical = aspect_ratio == "9:16"
    
    # Calculate margins based on aspect ratio (simple percentage)
    margin_x = int(width * 0.03)  # 3% of width
    margin_y = int(height * 0.03)  # 3% of height
    
    # Adjust font sizes for vertical
    if is_vertical:
        font_title_size = 56  # Smaller for vertical
        font_body_size = 32
        font_small_size = 24
        text_wrap_width = 45  # Shorter lines for vertical
    else:
        font_title_size = 72
        font_body_size = 40
        font_small_size = 28
        text_wrap_width = 85

    # ── Fonts ──────────────────────────────────────────────────────────────
    font_title, font_body, font_small = _load_fonts(font_title_size, font_body_size, font_small_size)

    # ── Accent bar (left edge) ─────────────────────────────────────────────
    bar_w = 12 if not is_vertical else 16  # Thicker bar for vertical
    draw.rectangle([0, 0, bar_w, height], fill=accent_col)

    # ── Scene number pill ──────────────────────────────────────────────────
    pill_text = f"SCENE {scene_number:02d}"
    pill_x, pill_y = margin_x + bar_w + 20, margin_y
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
    div_y = pill_y + pill_h + 20
    draw.line([(margin_x + bar_w, div_y), (width - margin_x, div_y)], 
              fill=(*accent_col, 120), width=2)

    # ── Title (script/video title, truncated) ──────────────────────────────
    title_text = title[:70] + ("…" if len(title) > 70 else "")
    draw.text((margin_x + bar_w + 20, div_y + 15), title_text, 
              fill=(*title_col, 220), font=font_title)

    # ── Narration excerpt ──────────────────────────────────────────────────
    # Wrap narration to width based on aspect ratio
    excerpt = narration.strip()
    if len(excerpt) > 500:
        excerpt = excerpt[:497] + "…"
    lines = textwrap.wrap(excerpt, width=text_wrap_width)[:6 if is_vertical else 6]
    narration_text = "\n".join(lines)

    body_y = div_y + 55 if not is_vertical else div_y + 70
    draw.multiline_text(
        (margin_x + bar_w + 20, body_y),
        narration_text,
        fill=body_col,
        font=font_body,
        spacing=12 if not is_vertical else 10,
    )

    # ── Visual hint (smaller, bottom area) ───────────────────────────────
    if visual_description and visual_description.strip():
        hint = f"🎥  {visual_description[:120 if not is_vertical else 80]}"
        hint_y = height - margin_y - 60
        draw.text(
            (margin_x + bar_w + 20, hint_y),
            hint,
            fill=(*accent_col, 160),
            font=font_small,
        )

    # ── Bottom divider ─────────────────────────────────────────────────────
    hint_y = height - margin_y - 60 if visual_description else height - margin_y - 40
    draw.line([(margin_x + bar_w, hint_y - 20), (width - margin_x, hint_y - 20)],
              fill=(*accent_col, 80), width=1)

    img.save(str(out), "PNG", optimize=False)
    logger.debug("Minimal dark scene card written: %s", out)
    return out


def _generate_quote_fact_scene_card(
    scene_number: int,
    title: str,
    narration: str,
    visual_description: str,
    output_path: str | Path,
    width: int,
    height: int,
    topic_seed: str,
    template: object,
    aspect_ratio: str = "16:9",  # Phase 3E.2
) -> Path:
    """Generate quote_fact scene card with large quote emphasis and aspect ratio support."""
    from PIL import Image, ImageDraw
    from backend.services.video.aspect_ratio import get_aspect_ratio_config

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    palette = _pick_palette(topic_seed or title)
    bg_top, bg_bottom, title_col, body_col, accent_col = palette

    img = _make_gradient(width, height, bg_top, bg_bottom)
    draw = ImageDraw.Draw(img)

    # Phase 3E.2: Get aspect ratio config for layout adjustments
    ar_config = get_aspect_ratio_config(aspect_ratio)
    is_vertical = aspect_ratio == "9:16"
    
    # Calculate margins based on aspect ratio (simple percentage)
    margin_x = int(width * 0.03)  # 3% of width
    margin_y = int(height * 0.03)  # 3% of height
    
    # Adjust font sizes for vertical
    if is_vertical:
        font_title_size = 72
        font_body_size = 44
        font_small_size = 28
        font_quote_size = 96  # Slightly smaller for vertical
        text_wrap_width = 40  # Shorter lines for vertical
    else:
        font_title_size = 84
        font_body_size = 48
        font_small_size = 32
        font_quote_size = 120
        text_wrap_width = 50

    # Bold fonts for quote template
    font_title, font_body, font_small = _load_fonts(font_title_size, font_body_size, font_small_size)
    font_quote = _load_fonts(font_quote_size, 64, 32)[0]  # Extra large for quotes

    # Thicker accent bar
    bar_w = 24 if not is_vertical else 32
    draw.rectangle([0, 0, bar_w, height], fill=accent_col)

    # Large quote marks at top
    quote_y = margin_y + 40 if not is_vertical else margin_y + 60
    draw.text((width // 2, quote_y), "❝", fill=(*accent_col, 180), font=font_quote, anchor="ma")

    # Extract key quote from narration (first 2-3 sentences)
    excerpt = narration.strip()
    if len(excerpt) > 300:
        excerpt = excerpt[:297] + "…"
    quote_lines = textwrap.wrap(excerpt, width=text_wrap_width)[:4 if is_vertical else 4]
    quote_text = "\n".join(quote_lines)

    # Centered quote text
    quote_y = quote_y + 100 if not is_vertical else quote_y + 120
    draw.multiline_text(
        (width // 2, quote_y),
        quote_text,
        fill=title_col,
        font=font_body,
        anchor="ma",
        align="center",
        spacing=18 if not is_vertical else 16,
    )

    # Scene number indicator (subtle)
    pill_text = f"{scene_number}"
    draw.text((width - margin_x - 40, height - margin_y - 80), pill_text, 
              fill=(*accent_col, 120), font=font_title, anchor="ma")

    # Visual hint (bottom)
    if visual_description and visual_description.strip():
        hint = f"🎥  {visual_description[:100 if not is_vertical else 60]}"
        hint_y = height - margin_y - 120
        draw.text(
            (width // 2, hint_y),
            hint,
            fill=(*accent_col, 140),
            font=font_small,
            anchor="ma",
        )

    img.save(str(out), "PNG", optimize=False)
    logger.debug("Quote fact scene card written: %s", out)
    return out
