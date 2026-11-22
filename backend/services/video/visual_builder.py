"""
backend/services/video/visual_builder.py — Pillow-based scene card generator.

P0.1: Scene/title cards are RGBA overlays:
  - Transparent canvas (background footage shows through)
  - Dark-glass panels behind text only (~0.55 opacity)
  - Opaque readable text
  - visual_description / prompts / emoji hints are NEVER drawn on-screen

P0.6 visual quality:
  - Title card: video title once only (hook via subtitles)
  - Scene cards: small scene-number pill only (no repeated title / narration panel)
  - Background fills most of the frame

Templates:
  - minimal_dark
  - quote_fact
"""

from __future__ import annotations

import hashlib
import logging
import textwrap
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Glass panel opacity for text readability (mid of 0.45–0.65 range)
_GLASS_OPACITY = 0.55
_GLASS_ALPHA = int(255 * _GLASS_OPACITY)

# Colour palettes: (accent, title_colour, body_colour)
# Background gradients are applied by background_processor, not on the overlay.
_PALETTES = [
    ((80, 160, 255), (255, 255, 255), (230, 240, 255)),   # blue
    ((60, 200, 210), (255, 255, 255), (220, 245, 248)),   # teal
    ((160, 100, 255), (255, 255, 255), (240, 230, 255)),  # purple
    ((60, 200, 100), (255, 255, 255), (220, 250, 230)),   # green
    ((255, 100, 110), (255, 255, 255), (255, 230, 235)),  # maroon
    ((130, 180, 230), (255, 255, 255), (235, 240, 248)),  # slate
]

# Legacy full palettes kept for gradient fallback baking in background_processor
_GRADIENT_PALETTES = [
    ((15, 25, 60), (25, 50, 110), (255, 255, 255), (200, 220, 255), (80, 160, 255)),
    ((10, 40, 45), (20, 75, 80), (255, 255, 255), (180, 230, 235), (60, 200, 210)),
    ((35, 15, 60), (65, 30, 100), (255, 255, 255), (220, 200, 255), (160, 100, 255)),
    ((15, 40, 25), (25, 75, 45), (255, 255, 255), (190, 240, 205), (60, 200, 100)),
    ((55, 15, 20), (100, 25, 35), (255, 255, 255), (255, 210, 215), (255, 100, 110)),
    ((30, 35, 45), (55, 65, 80), (255, 255, 255), (215, 220, 230), (130, 180, 230)),
]


def _pick_palette(seed: str) -> tuple:
    idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(_PALETTES)
    return _PALETTES[idx]


def pick_gradient_palette(seed: str) -> tuple:
    """Return (bg_top, bg_bottom, title, body, accent) for gradient fallback baking."""
    idx = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(_GRADIENT_PALETTES)
    return _GRADIENT_PALETTES[idx]


def _load_fonts(size_large: int, size_medium: int, size_small: int):
    """Load fonts with graceful fallback to default."""
    try:
        from PIL import ImageFont
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
    """Create a vertical gradient as an opaque RGB PIL Image (fallback backgrounds)."""
    from PIL import Image
    img = Image.new("RGB", (width, height))
    pixels = img.load()
    for y in range(height):
        t = y / max(height - 1, 1)
        r = int(top[0] + t * (bottom[0] - top[0]))
        g = int(top[1] + t * (bottom[1] - top[1]))
        b = int(top[2] + t * (bottom[2] - top[2]))
        for x in range(width):
            pixels[x, y] = (r, g, b)
    return img


def _new_overlay_canvas(width: int, height: int):
    """Fully transparent RGBA canvas for text overlays."""
    from PIL import Image
    return Image.new("RGBA", (width, height), (0, 0, 0, 0))


def _draw_glass_panel(img, box: tuple[int, int, int, int], radius: int = 24) -> None:
    """
    Draw a semi-transparent dark-glass rounded panel.
    box = (left, top, right, bottom). Only this region is darkened.
    """
    from PIL import Image, ImageDraw
    left, top, right, bottom = [int(v) for v in box]
    if right <= left or bottom <= top:
        return
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        [left, top, right, bottom],
        radius=radius,
        fill=(0, 0, 0, _GLASS_ALPHA),
    )
    img.alpha_composite(overlay)


def _text_block_size(draw, text: str, font, spacing: int = 12) -> tuple[int, int]:
    """Return (width, height) of a multiline text block."""
    if not text:
        return (0, 0)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="left")
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


def generate_scene_card(
    scene_number: int,
    title: str,
    narration: str,
    visual_description: str,
    output_path: str | Path,
    width: int = 1920,
    height: int = 1080,
    topic_seed: str = "",
    template: Optional[object] = None,
    aspect_ratio: str = "16:9",
) -> Path:
    """
    Generate a scene card PNG (RGBA overlay).

    visual_description is accepted for API compatibility but is never drawn.
    """
    if template is None:
        from backend.services.video.templates import get_default_template
        template = get_default_template()

    if template.template_id == "quote_fact":
        return _generate_quote_fact_scene_card(
            scene_number, title, narration, visual_description,
            output_path, width, height, topic_seed, template, aspect_ratio,
        )
    return _generate_minimal_dark_scene_card(
        scene_number, title, narration, visual_description,
        output_path, width, height, topic_seed, template, aspect_ratio,
    )


def generate_title_card(
    title: str,
    hook: str,
    output_path: str | Path,
    width: int = 1920,
    height: int = 1080,
    topic_seed: str = "",
    template: Optional[object] = None,
    aspect_ratio: str = "16:9",
) -> Path:
    """Generate an opening title card (RGBA overlay)."""
    if template is None:
        from backend.services.video.templates import get_default_template
        template = get_default_template()

    if template.template_id == "quote_fact":
        return _generate_quote_fact_title_card(
            title, hook, output_path, width, height, topic_seed, template, aspect_ratio,
        )
    return _generate_minimal_dark_title_card(
        title, hook, output_path, width, height, topic_seed, template, aspect_ratio,
    )


def _generate_minimal_dark_title_card(
    title: str,
    hook: str,
    output_path: str | Path,
    width: int,
    height: int,
    topic_seed: str,
    template: object,
    aspect_ratio: str = "16:9",
) -> Path:
    """
    P0.6: Title card shows the video title once only.
    Opening hook is spoken + subtitled — not drawn here (avoids overlap/duplication).
    """
    from PIL import ImageDraw

    del hook  # hook is reserved for narration/subtitles, not overlay text

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    accent_col, title_col, _body_col = _pick_palette(topic_seed or title)
    img = _new_overlay_canvas(width, height)
    draw = ImageDraw.Draw(img)
    is_vertical = aspect_ratio == "9:16"

    if is_vertical:
        font_title_size, text_wrap_width = 64, 26
        panel_w_ratio, panel_pad = 0.86, 32
    else:
        font_title_size, text_wrap_width = 68, 34
        panel_w_ratio, panel_pad = 0.70, 36

    font_title, _font_body, _font_small = _load_fonts(font_title_size, 32, 24)

    # Thin accent bar
    bar_w = 10 if not is_vertical else 12
    bar = _new_overlay_canvas(width, height)
    ImageDraw.Draw(bar).rectangle([0, 0, bar_w, height], fill=(*accent_col, 200))
    img.alpha_composite(bar)

    title_lines = textwrap.wrap(title.strip(), width=text_wrap_width)[:3]
    title_text = "\n".join(title_lines) or " "
    tw, th = _text_block_size(draw, title_text, font_title, spacing=12)

    panel_w = max(int(width * panel_w_ratio), tw + panel_pad * 2)
    panel_h = th + panel_pad * 2
    panel_left = (width - panel_w) // 2
    # Upper third — leave mid/lower frame clear for opening-hook subtitles
    panel_top = max(int(height * 0.16) - panel_h // 2, int(height * 0.06))
    panel_right = panel_left + panel_w
    panel_bottom = min(panel_top + panel_h, int(height * 0.42))

    _draw_glass_panel(img, (panel_left, panel_top, panel_right, panel_bottom), radius=22)

    draw = ImageDraw.Draw(img)
    draw.multiline_text(
        (width // 2, panel_top + panel_pad),
        title_text,
        fill=(*title_col, 255),
        font=font_title,
        anchor="ma",
        align="center",
        spacing=12,
    )

    img.save(str(out), "PNG", optimize=False)
    logger.debug("Minimal dark title overlay written: %s", out)
    return out


def _generate_quote_fact_title_card(
    title: str,
    hook: str,
    output_path: str | Path,
    width: int,
    height: int,
    topic_seed: str,
    template: object,
    aspect_ratio: str = "16:9",
) -> Path:
    """P0.6: Title once only — hook stays in narration/subtitles."""
    from PIL import ImageDraw

    del hook

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    accent_col, title_col, _body_col = _pick_palette(topic_seed or title)
    img = _new_overlay_canvas(width, height)
    is_vertical = aspect_ratio == "9:16"

    if is_vertical:
        font_title_size, text_wrap_width = 68, 24
        panel_w_ratio, panel_pad = 0.88, 36
    else:
        font_title_size, text_wrap_width = 72, 30
        panel_w_ratio, panel_pad = 0.72, 40

    font_title, _font_body, _font_small = _load_fonts(font_title_size, 36, 28)

    bar_w = 16 if not is_vertical else 20
    bar = _new_overlay_canvas(width, height)
    ImageDraw.Draw(bar).rectangle([0, 0, bar_w, height], fill=(*accent_col, 220))
    img.alpha_composite(bar)

    draw = ImageDraw.Draw(img)
    title_lines = textwrap.wrap(title.strip(), width=text_wrap_width)[:3]
    title_text = "\n".join(title_lines) or " "
    tw, th = _text_block_size(draw, title_text, font_title, spacing=14)

    panel_w = max(int(width * panel_w_ratio), tw + panel_pad * 2)
    panel_h = th + panel_pad * 2
    panel_left = (width - panel_w) // 2
    panel_top = max(int(height * 0.16) - panel_h // 2, int(height * 0.06))
    panel_right = panel_left + panel_w
    panel_bottom = min(panel_top + panel_h, int(height * 0.42))

    _draw_glass_panel(img, (panel_left, panel_top, panel_right, panel_bottom), radius=28)

    draw = ImageDraw.Draw(img)
    draw.multiline_text(
        (width // 2, panel_top + panel_pad),
        title_text,
        fill=(*title_col, 255),
        font=font_title,
        anchor="ma",
        align="center",
        spacing=14,
    )

    img.save(str(out), "PNG", optimize=False)
    logger.debug("Quote fact title overlay written: %s", out)
    return out


def _generate_minimal_dark_scene_card(
    scene_number: int,
    title: str,
    narration: str,
    visual_description: str,  # unused on-screen (API compat)
    output_path: str | Path,
    width: int,
    height: int,
    topic_seed: str,
    template: object,
    aspect_ratio: str = "16:9",
) -> Path:
    """
    P0.6: Scene overlay is a small unobtrusive label only.
    No repeated video title, no narration panel (subtitles carry speech).
    Background fills most of the frame.
    """
    from PIL import ImageDraw

    del visual_description, title, narration  # never drawn on scene cards (P0.6)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    accent_col, _title_col, _body_col = _pick_palette(topic_seed or f"scene-{scene_number}")
    img = _new_overlay_canvas(width, height)
    is_vertical = aspect_ratio == "9:16"

    # P0.6.1: keep label fully inside frame after Ken Burns zoom (~1.05).
    # Glass pad must also stay inside the safe rectangle.
    glass_pad_x, glass_pad_y = 8, 6
    if is_vertical:
        safe_x = max(56, int(width * 0.06))
        safe_y = max(64, int(height * 0.05))
        font_small_size = 20
    else:
        safe_x = max(56, int(width * 0.045))
        safe_y = max(48, int(height * 0.045))
        font_small_size = 18
    _ft, _fb, font_small = _load_fonts(40, 28, font_small_size)

    draw = ImageDraw.Draw(img)
    pill_text = f"{scene_number:02d}"
    pill_bbox = draw.textbbox((0, 0), pill_text, font=font_small)
    pill_w = pill_bbox[2] - pill_bbox[0] + 20
    pill_h = pill_bbox[3] - pill_bbox[1] + 10

    x0 = safe_x + glass_pad_x
    y0 = safe_y + glass_pad_y
    glass_box = (
        max(0, x0 - glass_pad_x),
        max(0, y0 - glass_pad_y),
        min(width, x0 + pill_w + glass_pad_x),
        min(height, y0 + pill_h + glass_pad_y),
    )
    _draw_glass_panel(img, glass_box, radius=10)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [x0, y0, x0 + pill_w, y0 + pill_h],
        radius=7,
        fill=(*accent_col, 200),
    )
    draw.text((x0 + 10, y0 + 3), pill_text, fill=(255, 255, 255, 255), font=font_small)

    img.save(str(out), "PNG", optimize=False)
    logger.debug("Minimal dark scene overlay written: %s", out)
    return out


def _generate_quote_fact_scene_card(
    scene_number: int,
    title: str,
    narration: str,
    visual_description: str,  # unused on-screen (API compat)
    output_path: str | Path,
    width: int,
    height: int,
    topic_seed: str,
    template: object,
    aspect_ratio: str = "16:9",
) -> Path:
    """
    P0.6: Same minimal scene chrome as minimal_dark — no quote panel
    duplicating narration (subtitles carry speech).
    """
    return _generate_minimal_dark_scene_card(
        scene_number=scene_number,
        title=title,
        narration=narration,
        visual_description=visual_description,
        output_path=output_path,
        width=width,
        height=height,
        topic_seed=topic_seed,
        template=template,
        aspect_ratio=aspect_ratio,
    )
