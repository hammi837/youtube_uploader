"""
backend/services/video/visual_builder.py — Pillow-based scene card generator.

P0.1: Scene/title cards are RGBA overlays:
  - Transparent canvas (background footage shows through)
  - Dark-glass panels behind text only (~0.55 opacity)
  - Opaque readable text
  - visual_description / prompts / emoji hints are NEVER drawn on-screen

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
    from PIL import ImageDraw

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    accent_col, title_col, body_col = _pick_palette(topic_seed or title)
    img = _new_overlay_canvas(width, height)
    draw = ImageDraw.Draw(img)
    is_vertical = aspect_ratio == "9:16"

    if is_vertical:
        font_title_size, font_body_size, text_wrap_width = 72, 40, 28
        panel_w_ratio, panel_pad = 0.88, 36
    else:
        font_title_size, font_body_size, text_wrap_width = 80, 40, 36
        panel_w_ratio, panel_pad = 0.72, 40

    font_title, font_body, _font_small = _load_fonts(font_title_size, font_body_size, 28)

    # Accent bar (semi-opaque)
    bar_w = 12 if not is_vertical else 16
    bar = _new_overlay_canvas(width, height)
    ImageDraw.Draw(bar).rectangle([0, 0, bar_w, height], fill=(*accent_col, 220))
    img.alpha_composite(bar)

    title_lines = textwrap.wrap(title.strip(), width=text_wrap_width)[:3]
    title_text = "\n".join(title_lines) or " "
    hook_text = ""
    if hook and hook.strip():
        hook_text = "\n".join(textwrap.wrap(hook.strip()[:180], width=int(text_wrap_width * 1.4))[:3])

    tw, th = _text_block_size(draw, title_text, font_title, spacing=14)
    hw, hh = _text_block_size(draw, hook_text, font_body, spacing=10) if hook_text else (0, 0)
    content_h = th + (24 + hh if hook_text else 0)
    content_w = max(tw, hw)

    panel_w = max(int(width * panel_w_ratio), content_w + panel_pad * 2)
    panel_h = content_h + panel_pad * 2
    panel_left = (width - panel_w) // 2
    panel_top = max((height - panel_h) // 2 - (40 if not is_vertical else 20), int(height * 0.12))
    panel_right = panel_left + panel_w
    panel_bottom = panel_top + panel_h

    # Keep lower caption band clear
    max_bottom = int(height * 0.78)
    if panel_bottom > max_bottom:
        shift = panel_bottom - max_bottom
        panel_top = max(int(height * 0.08), panel_top - shift)
        panel_bottom = panel_top + panel_h

    _draw_glass_panel(img, (panel_left, panel_top, panel_right, panel_bottom), radius=28)

    draw = ImageDraw.Draw(img)
    title_x = width // 2
    title_y = panel_top + panel_pad
    draw.multiline_text(
        (title_x, title_y),
        title_text,
        fill=(*title_col, 255),
        font=font_title,
        anchor="ma",
        align="center",
        spacing=14,
    )
    if hook_text:
        draw.multiline_text(
            (title_x, title_y + th + 24),
            hook_text,
            fill=(*body_col, 255),
            font=font_body,
            anchor="ma",
            align="center",
            spacing=10,
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
    from PIL import ImageDraw

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    accent_col, title_col, body_col = _pick_palette(topic_seed or title)
    img = _new_overlay_canvas(width, height)
    is_vertical = aspect_ratio == "9:16"

    if is_vertical:
        font_title_size, font_body_size, text_wrap_width = 80, 48, 26
        panel_w_ratio, panel_pad = 0.90, 40
    else:
        font_title_size, font_body_size, text_wrap_width = 88, 48, 32
        panel_w_ratio, panel_pad = 0.74, 44

    font_title, font_body, _font_small = _load_fonts(font_title_size, font_body_size, 32)

    bar_w = 24 if not is_vertical else 32
    bar = _new_overlay_canvas(width, height)
    ImageDraw.Draw(bar).rectangle([0, 0, bar_w, height], fill=(*accent_col, 230))
    img.alpha_composite(bar)

    draw = ImageDraw.Draw(img)
    title_lines = textwrap.wrap(title.strip(), width=text_wrap_width)[:3]
    title_text = "\n".join(title_lines) or " "
    hook_text = ""
    if hook and hook.strip():
        hook_text = "\n".join(textwrap.wrap(hook.strip()[:180], width=int(text_wrap_width * 1.5))[:3])

    tw, th = _text_block_size(draw, title_text, font_title, spacing=16)
    hw, hh = _text_block_size(draw, hook_text, font_body, spacing=12) if hook_text else (0, 0)
    content_h = th + (28 + hh if hook_text else 0)
    content_w = max(tw, hw)

    panel_w = max(int(width * panel_w_ratio), content_w + panel_pad * 2)
    panel_h = content_h + panel_pad * 2
    panel_left = (width - panel_w) // 2
    panel_top = max((height - panel_h) // 2 - 50, int(height * 0.10))
    panel_right = panel_left + panel_w
    panel_bottom = min(panel_top + panel_h, int(height * 0.78))

    _draw_glass_panel(img, (panel_left, panel_top, panel_right, panel_bottom), radius=32)

    draw = ImageDraw.Draw(img)
    title_x = width // 2
    title_y = panel_top + panel_pad
    draw.multiline_text(
        (title_x, title_y),
        title_text,
        fill=(*title_col, 255),
        font=font_title,
        anchor="ma",
        align="center",
        spacing=16,
    )
    if hook_text:
        draw.multiline_text(
            (title_x, title_y + th + 28),
            hook_text,
            fill=(*accent_col, 255),
            font=font_body,
            anchor="ma",
            align="center",
            spacing=12,
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
    from PIL import ImageDraw

    del visual_description  # never drawn — internal generation metadata only

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    accent_col, title_col, body_col = _pick_palette(topic_seed or title)
    img = _new_overlay_canvas(width, height)
    is_vertical = aspect_ratio == "9:16"

    margin_x = int(width * 0.04)
    margin_y = int(height * 0.04)

    if is_vertical:
        font_title_size, font_body_size, font_small_size = 48, 30, 22
        text_wrap_width = 28
        max_narr_lines = 5
    else:
        font_title_size, font_body_size, font_small_size = 56, 34, 24
        text_wrap_width = 48
        max_narr_lines = 4

    font_title, font_body, font_small = _load_fonts(font_title_size, font_body_size, font_small_size)

    # Accent bar
    bar_w = 12 if not is_vertical else 16
    bar = _new_overlay_canvas(width, height)
    ImageDraw.Draw(bar).rectangle([0, 0, bar_w, height], fill=(*accent_col, 220))
    img.alpha_composite(bar)

    draw = ImageDraw.Draw(img)

    # Leave bottom ~22% clear for burned captions
    caption_clear_y = int(height * 0.78)

    pill_text = f"SCENE {scene_number:02d}"
    title_text = (title[:64] + ("…" if len(title) > 64 else "")).strip() or " "
    excerpt = narration.strip()
    if len(excerpt) > 320:
        excerpt = excerpt[:317] + "…"
    narr_lines = textwrap.wrap(excerpt, width=text_wrap_width)[:max_narr_lines]
    narration_text = "\n".join(narr_lines) if narr_lines else " "

    pad = 28 if not is_vertical else 24
    spacing = 10 if not is_vertical else 8

    # Measure content
    pill_bbox = draw.textbbox((0, 0), pill_text, font=font_small)
    pill_w = pill_bbox[2] - pill_bbox[0] + 28
    pill_h = pill_bbox[3] - pill_bbox[1] + 14
    tw, th = _text_block_size(draw, title_text, font_title, spacing=4)
    nw, nh = _text_block_size(draw, narration_text, font_body, spacing=spacing)

    content_w = max(pill_w, tw, nw)
    panel_left = margin_x + bar_w + 8
    panel_right = min(width - margin_x, panel_left + content_w + pad * 2)
    # Prefer a readable panel width on wide frames
    if not is_vertical:
        panel_right = max(panel_right, int(width * 0.62))
        panel_right = min(panel_right, width - margin_x)

    panel_top = margin_y
    inner_h = pad + pill_h + 16 + th + 18 + nh + pad
    panel_bottom = min(panel_top + inner_h, caption_clear_y)
    if panel_bottom - panel_top < inner_h:
        # Shrink narration further if needed
        while narr_lines and (panel_top + inner_h) > caption_clear_y:
            narr_lines = narr_lines[:-1]
            narration_text = "\n".join(narr_lines) if narr_lines else " "
            nw, nh = _text_block_size(draw, narration_text, font_body, spacing=spacing)
            inner_h = pad + pill_h + 16 + th + 18 + nh + pad
        panel_bottom = min(panel_top + inner_h, caption_clear_y)

    _draw_glass_panel(img, (panel_left, panel_top, panel_right, panel_bottom), radius=20)

    draw = ImageDraw.Draw(img)
    x0 = panel_left + pad
    y = panel_top + pad

    # Scene pill
    draw.rounded_rectangle(
        [x0, y, x0 + pill_w, y + pill_h],
        radius=8,
        fill=(*accent_col, 230),
    )
    draw.text((x0 + 14, y + 4), pill_text, fill=(255, 255, 255, 255), font=font_small)
    y += pill_h + 16

    draw.text((x0, y), title_text, fill=(*title_col, 255), font=font_title)
    y += th + 18

    draw.multiline_text(
        (x0, y),
        narration_text,
        fill=(*body_col, 255),
        font=font_body,
        spacing=spacing,
    )

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
    from PIL import ImageDraw

    del visual_description
    del title  # quote template emphasizes narration, not title chrome

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    accent_col, title_col, _body_col = _pick_palette(topic_seed or "quote")
    img = _new_overlay_canvas(width, height)
    is_vertical = aspect_ratio == "9:16"

    margin_x = int(width * 0.05)
    margin_y = int(height * 0.06)
    caption_clear_y = int(height * 0.78)

    if is_vertical:
        font_body_size, font_small_size, font_quote_size = 40, 26, 84
        text_wrap_width = 26
        max_lines = 5
        panel_w_ratio = 0.88
    else:
        font_body_size, font_small_size, font_quote_size = 44, 28, 100
        text_wrap_width = 42
        max_lines = 4
        panel_w_ratio = 0.70

    _ft, font_body, font_small = _load_fonts(56, font_body_size, font_small_size)
    font_quote = _load_fonts(font_quote_size, 64, 32)[0]

    bar_w = 24 if not is_vertical else 32
    bar = _new_overlay_canvas(width, height)
    ImageDraw.Draw(bar).rectangle([0, 0, bar_w, height], fill=(*accent_col, 230))
    img.alpha_composite(bar)

    draw = ImageDraw.Draw(img)
    excerpt = narration.strip()
    if len(excerpt) > 280:
        excerpt = excerpt[:277] + "…"
    quote_lines = textwrap.wrap(excerpt, width=text_wrap_width)[:max_lines]
    quote_text = "\n".join(quote_lines) if quote_lines else " "

    qw, qh = _text_block_size(draw, quote_text, font_body, spacing=16)
    mark_h = font_quote_size

    panel_w = max(int(width * panel_w_ratio), qw + 80)
    panel_h = mark_h + 24 + qh + 64
    panel_left = (width - panel_w) // 2
    panel_top = max(margin_y + 20, int(height * 0.12))
    panel_right = panel_left + panel_w
    panel_bottom = min(panel_top + panel_h, caption_clear_y)

    _draw_glass_panel(img, (panel_left, panel_top, panel_right, panel_bottom), radius=28)

    draw = ImageDraw.Draw(img)
    cx = width // 2
    draw.text((cx, panel_top + 16), "\u201C", fill=(*accent_col, 230), font=font_quote, anchor="ma")
    draw.multiline_text(
        (cx, panel_top + mark_h + 8),
        quote_text,
        fill=(*title_col, 255),
        font=font_body,
        anchor="ma",
        align="center",
        spacing=16,
    )

    # Subtle scene number — inside glass, not a prompt
    draw.text(
        (panel_right - 28, panel_bottom - 28),
        f"{scene_number}",
        fill=(*accent_col, 200),
        font=font_small,
        anchor="rb",
    )

    img.save(str(out), "PNG", optimize=False)
    logger.debug("Quote fact scene overlay written: %s", out)
    return out
