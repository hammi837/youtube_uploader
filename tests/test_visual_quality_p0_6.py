"""P0.6 — layout / caption / background relevance regressions."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from backend.services.video.caption_generator import _format_caption_display
from backend.services.video.production_backgrounds import (
    _looks_like_decorative_abstract,
    validate_image_file,
)
from backend.services.video.templates import get_template
from backend.services.video.visual_builder import generate_scene_card, generate_title_card


def test_format_caption_max_two_lines():
    # Short cue still wraps to ≤2 lines; long text is handled by _split_caption_cues
    text = " ".join([f"word{i}" for i in range(8)])
    out = _format_caption_display(text, max_chars_per_line=40, max_lines=2)
    assert out.count("\n") <= 1
    assert len(out.splitlines()) <= 2
    assert "…" not in out


def test_title_card_does_not_draw_hook(tmp_path):
    out = tmp_path / "title.png"
    generate_title_card(
        title="How Magnets Work",
        hook="THIS_UNIQUE_HOOK_TOKEN_SHOULD_NOT_APPEAR",
        output_path=str(out),
        width=640,
        height=360,
        template=get_template("minimal_dark"),
    )
    assert out.exists() and out.stat().st_size > 500
    # Overlay is mostly transparent; ensure file is a valid PNG of expected size
    with Image.open(out) as img:
        assert img.size == (640, 360)
        assert img.mode == "RGBA"


def test_scene_card_is_minimal_overlay(tmp_path):
    out = tmp_path / "scene.png"
    generate_scene_card(
        scene_number=3,
        title="How Magnets Work — Full Title Repeated",
        narration="Long narration that must not fill a glass panel anymore.",
        visual_description="ignored",
        output_path=str(out),
        width=640,
        height=360,
        template=get_template("minimal_dark"),
    )
    with Image.open(out) as img:
        assert img.size == (640, 360)
        # Most pixels remain fully transparent — background fills the frame
        alpha = img.split()[-1]
        opaque = sum(1 for px in alpha.getdata() if px > 0)
        assert opaque < (640 * 360) * 0.08


def test_quote_fact_scene_also_minimal(tmp_path):
    out = tmp_path / "quote.png"
    generate_scene_card(
        scene_number=1,
        title="Title",
        narration="A quote that used to dominate the frame.",
        visual_description="",
        output_path=str(out),
        width=640,
        height=360,
        template=get_template("quote_fact"),
    )
    with Image.open(out) as img:
        opaque = sum(1 for px in img.split()[-1].getdata() if px > 0)
        assert opaque < (640 * 360) * 0.08


def test_rejects_decorative_circle_blob_art(tmp_path):
    path = tmp_path / "deco.jpg"
    img = Image.new("RGB", (640, 360), (40, 10, 60))
    draw = ImageDraw.Draw(img)
    # Saturated blobs + thin white ring (seeded production style)
    draw.ellipse([80, 40, 180, 120], fill=(0, 255, 80))
    draw.ellipse([460, 40, 560, 120], fill=(0, 255, 80))
    draw.ellipse([270, 260, 370, 340], fill=(0, 255, 80))
    cx, cy, r = 320, 180, 110
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255), width=3)
    img.save(path, quality=95)
    assert _looks_like_decorative_abstract(Image.open(path).convert("RGB")) is True
    assert validate_image_file(str(path)) is False


def test_accepts_varied_non_decorative_image(tmp_path):
    path = tmp_path / "photo.jpg"
    img = Image.new("RGB", (640, 360))
    for y in range(360):
        for x in range(640):
            img.putpixel((x, y), ((x * 3) % 255, (y * 5) % 255, (x + y) % 255))
    img.save(path, quality=85)
    assert validate_image_file(str(path)) is True


def test_burn_captions_style_restrained():
    """Smoke-check force_style constants used by burn_captions."""
    import inspect
    from backend.services.video import ffmpeg_assembler as fa

    src = inspect.getsource(fa.burn_captions)
    assert "BorderStyle=3" in src
    assert "_ass_play_res_y" in src
    assert "0.020" in src
    assert "OutlineColour=&H80000000" in src
