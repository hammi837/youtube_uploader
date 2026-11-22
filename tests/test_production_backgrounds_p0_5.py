"""P0.5 — production background selection defaults."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from backend.services.video.production_backgrounds import (
    PRODUCTION_AUTO,
    apply_production_backgrounds,
    resolve_job_background_type,
    validate_image_file,
)


def test_resolve_none_to_production_auto(monkeypatch):
    monkeypatch.delenv("QUEUE_DEFAULT_BACKGROUND", raising=False)
    assert resolve_job_background_type(None) == PRODUCTION_AUTO
    assert resolve_job_background_type("") == PRODUCTION_AUTO
    assert resolve_job_background_type("  ") == PRODUCTION_AUTO


def test_resolve_explicit_passthrough():
    assert resolve_job_background_type("gradient") == "gradient"
    assert resolve_job_background_type("local_image_auto") == "local_image_auto"


def test_validate_rejects_missing_and_tiny(tmp_path):
    assert validate_image_file(None) is False
    assert validate_image_file(str(tmp_path / "nope.jpg")) is False
    tiny = tmp_path / "tiny.jpg"
    tiny.write_bytes(b"not-an-image")
    assert validate_image_file(str(tiny)) is False


def test_validate_accepts_varied_image(tmp_path):
    path = tmp_path / "ok.jpg"
    img = Image.new("RGB", (640, 360))
    for y in range(360):
        for x in range(640):
            img.putpixel((x, y), (x % 255, y % 255, (x + y) % 255))
    img.save(path, quality=85)
    assert validate_image_file(str(path)) is True


def test_validate_rejects_flat_gray(tmp_path):
    path = tmp_path / "gray.jpg"
    Image.new("RGB", (640, 360), (128, 128, 128)).save(path, quality=90)
    assert validate_image_file(str(path)) is False


def test_validate_rejects_flat_cyan_plate(tmp_path):
    path = tmp_path / "cyan.jpg"
    Image.new("RGB", (800, 450), (0, 247, 255)).save(path, quality=90)
    assert validate_image_file(str(path)) is False


def test_validate_rejects_near_flat_gray_jpeg_noise(tmp_path):
    path = tmp_path / "grayish.jpg"
    # Simulate Phase-3F fixture: nearly flat gray with tiny JPEG-like noise
    img = Image.new("RGB", (640, 360), (80, 80, 80))
    img.putpixel((10, 10), (90, 90, 90))
    img.putpixel((20, 20), (70, 70, 70))
    img.save(path, quality=85)
    assert validate_image_file(str(path)) is False


def test_apply_uses_local_then_skips_ai(tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCTION_BG_AI", "true")
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    # Create two colorful images
    paths = []
    for name, base in [
        ("earth magnetic field.png", (20, 80, 200)),
        ("magnet attract iron.png", (200, 60, 40)),
    ]:
        im = Image.new("RGB", (800, 450))
        for y in range(450):
            for x in range(800):
                im.putpixel(
                    (x, y),
                    ((base[0] + x) % 255, (base[1] + y) % 255, (base[2] + x // 3) % 255),
                )
        p = img_dir / name
        im.save(p)
        assert validate_image_file(str(p)), name
        paths.append(p)

    scenes = [
        {"narration": "Earth magnetic field protects us", "visual_prompt": "earth magnetic field"},
        {"narration": "How magnets attract iron", "visual_prompt": "magnet attract iron"},
    ]

    with patch(
        "backend.services.video.image_asset_discovery.discover_image_assets"
    ) as disc, patch(
        "backend.services.visual.visual_provider.generate_visual_asset"
    ) as ai:
        from backend.services.video.image_asset_discovery import ImageMetadata

        disc.return_value = [
            ImageMetadata(path=str(paths[0]), filename=paths[0].name, width=800, height=450),
            ImageMetadata(path=str(paths[1]), filename=paths[1].name, width=800, height=450),
        ]
        counts = apply_production_backgrounds(
            job_id="job-local",
            scenes=scenes,
            aspect_ratio="16:9",
            topic="magnets",
        )
        ai.assert_not_called()
        assert counts["local_image"] == 2
        assert counts["gradient"] == 0
        assert scenes[0]["background_type"] == "local_image"
        assert scenes[0]["background_path"] != scenes[1]["background_path"]


def test_apply_ai_then_gradient_on_failure(monkeypatch):
    monkeypatch.setenv("PRODUCTION_BG_AI", "true")
    scenes = [{"narration": "test", "visual_prompt": "aurora sky"}]

    with patch(
        "backend.services.video.image_asset_discovery.discover_image_assets",
        return_value=[],
    ), patch(
        "backend.services.video.video_asset_discovery.select_auto_backgrounds",
        return_value=[None],
    ), patch(
        "backend.services.visual.visual_provider.generate_visual_asset"
    ) as ai:
        from backend.services.visual.visual_provider import VisualAssetResult

        ai.return_value = VisualAssetResult(
            asset_path=None,
            asset_type="image",
            provider_name="ai",
            fallback_used=True,
            error_message="timeout",
        )
        counts = apply_production_backgrounds(
            job_id="job-ai-fail",
            scenes=scenes,
            aspect_ratio="16:9",
            topic="magnets",
        )
        assert counts["gradient"] == 1
        assert scenes[0]["background_type"] == "gradient"
        assert scenes[0].get("background_path") is None


def test_never_sets_placeholder(monkeypatch):
    monkeypatch.setenv("PRODUCTION_BG_AI", "false")
    scenes = [{"narration": "x", "background_type": "placeholder"}]
    with patch(
        "backend.services.video.image_asset_discovery.discover_image_assets",
        return_value=[],
    ), patch(
        "backend.services.video.video_asset_discovery.select_auto_backgrounds",
        return_value=[None],
    ):
        apply_production_backgrounds(
            job_id="job-ph",
            scenes=scenes,
            aspect_ratio="16:9",
        )
    assert scenes[0]["background_type"] == "gradient"
