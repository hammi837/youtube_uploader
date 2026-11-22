"""P0.6.1 — scene-label safe margins + caption cue splitting (no truncation)."""
from __future__ import annotations

from PIL import Image

from backend.services.video.caption_generator import (
    _format_caption_display,
    _split_caption_cues,
    generate_fallback_captions,
)
from backend.services.video.templates import get_template
from backend.services.video.visual_builder import generate_scene_card


def _scene_label_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """Bounding box of non-transparent pixels (the scene pill)."""
    alpha = img.split()[-1]
    extrema = alpha.getbbox()
    assert extrema is not None, "scene label missing"
    return extrema  # left, top, right, bottom


def test_scene_label_safe_margins_16_9(tmp_path):
    out = tmp_path / "s169.png"
    generate_scene_card(
        scene_number=6,
        title="Title",
        narration="Narration",
        visual_description="",
        output_path=str(out),
        width=1920,
        height=1080,
        template=get_template("minimal_dark"),
        aspect_ratio="16:9",
    )
    with Image.open(out) as img:
        left, top, right, bottom = _scene_label_bbox(img)
        # Survive ~1.05 center zoom (~2.4% edge crop) with headroom
        assert left >= 48
        assert top >= 40
        assert right < 1920 - 20
        assert bottom < 1080 - 20


def test_scene_label_safe_margins_9_16(tmp_path):
    out = tmp_path / "s916.png"
    generate_scene_card(
        scene_number=6,
        title="Title",
        narration="Narration",
        visual_description="",
        output_path=str(out),
        width=1080,
        height=1920,
        template=get_template("minimal_dark"),
        aspect_ratio="9:16",
    )
    with Image.open(out) as img:
        left, top, right, bottom = _scene_label_bbox(img)
        assert left >= 48
        assert top >= 48
        assert right < 1080 - 20
        assert bottom < 1920 - 20


def test_split_caption_preserves_all_words():
    text = (
        "The magnetic field circles the current in a transformer "
        "one coils field induces a voltage in another powering everything"
    )
    cues = _split_caption_cues(text, 10.0, 20.0, max_chars_per_line=40, max_lines=2)
    assert len(cues) >= 2
    joined = " ".join(c[2].replace("\n", " ") for c in cues)
    assert "…" not in joined and "..." not in joined
    assert joined.split() == text.split()
    # Contiguous timing covering the parent window
    assert abs(cues[0][0] - 10.0) < 1e-6
    assert abs(cues[-1][1] - 20.0) < 1e-6
    for i in range(len(cues) - 1):
        assert cues[i][1] <= cues[i + 1][0] + 1e-9
    for _s, _e, display in cues:
        assert len(display.splitlines()) <= 2


def test_format_caption_max_two_lines_short():
    text = "short caption that fits"
    out = _format_caption_display(text, max_chars_per_line=40, max_lines=2)
    assert len(out.splitlines()) <= 2
    assert "…" not in out


def test_fallback_captions_preserve_all_words(tmp_path):
    text = (
        "Opposite poles attract like poles repel and compasses align "
        "with magnetic north while the magnetosphere shields Earth"
    )
    out = tmp_path / "fb.srt"
    generate_fallback_captions(text, audio_duration=12.0, output_srt_path=str(out))
    body = out.read_text(encoding="utf-8")
    # Strip SRT indices/timestamps; keep cue text lines only
    cue_words: list[str] = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 3:
            cue_words.extend(" ".join(lines[2:]).replace("\n", " ").split())
    assert cue_words == text.split()
    assert "…" not in body


def test_burn_captions_style_has_box_not_thick_outline():
    import inspect
    from backend.services.video import ffmpeg_assembler as fa

    src = inspect.getsource(fa.burn_captions)
    assert "BorderStyle=3" in src
    assert "_ass_play_res_y" in src
    assert "OutlineColour=&H80000000" in src
