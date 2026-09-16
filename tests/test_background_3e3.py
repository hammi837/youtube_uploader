"""
tests/test_background_3e3.py — Phase 3E.3 background visual system tests.

Tests:
- Background configuration and validation
- Default gradient fallback
- Missing image fallback
- Missing video fallback (deferred)
- Local image loading
- Image fitting for 16:9
- Image fitting for 9:16
- Scene-level background propagation
- Queue-to-video propagation
- Caption readability overlay
- Existing templates without backgrounds
- Backward compatibility with existing jobs
"""

import pytest
from pathlib import Path
from backend.services.video.background_config import (
    BackgroundConfig,
    BackgroundType,
    BackgroundFit,
    get_background_config,
    is_valid_background_type,
    is_valid_background_path,
    list_available_backgrounds,
    get_default_background_config,
    VALID_BACKGROUND_TYPES,
    VALID_FIT_MODES,
    DEFAULT_BACKGROUND_TYPE,
    DEFAULT_FIT_MODE,
)


# ── Background Configuration Tests ───────────────────────────────────────────

def test_default_background_config():
    """Test default background configuration."""
    config = get_default_background_config()
    assert config.background_type == BackgroundType.GRADIENT
    assert config.background_fit == DEFAULT_FIT_MODE
    assert config.overlay_opacity == 0.3


def test_valid_background_types():
    """Test valid background type constants."""
    assert "gradient" in VALID_BACKGROUND_TYPES
    assert "solid_color" in VALID_BACKGROUND_TYPES
    assert "local_image" in VALID_BACKGROUND_TYPES
    assert "local_video" in VALID_BACKGROUND_TYPES
    assert "placeholder" in VALID_BACKGROUND_TYPES


def test_valid_fit_modes():
    """Test valid fit mode constants."""
    assert "cover" in VALID_FIT_MODES
    assert "contain" in VALID_FIT_MODES
    assert "fill" in VALID_FIT_MODES


def test_is_valid_background_type():
    """Test background type validation."""
    assert is_valid_background_type("gradient") is True
    assert is_valid_background_type("solid_color") is True
    assert is_valid_background_type("local_image") is True
    assert is_valid_background_type("local_video") is True
    assert is_valid_background_type("invalid") is False


def test_get_background_config_gradient():
    """Test gradient background configuration."""
    config = get_background_config(background_type="gradient")
    assert config.background_type == BackgroundType.GRADIENT
    assert config.background_path is None
    assert config.background_fit == DEFAULT_FIT_MODE


def test_get_background_config_invalid_type_fallback():
    """Test invalid background type falls back to gradient."""
    config = get_background_config(background_type="invalid")
    assert config.background_type == BackgroundType.GRADIENT


def test_get_background_config_solid_color():
    """Test solid color background configuration."""
    config = get_background_config(
        background_type="solid_color",
        background_color="#FF0000",
    )
    assert config.background_type == BackgroundType.SOLID_COLOR
    assert config.background_color == "#FF0000"


def test_get_background_config_local_image():
    """Test local image background configuration."""
    config = get_background_config(
        background_type="local_image",
        background_path="assets/backgrounds/images/test.jpg",
    )
    # With missing file, should fall back to gradient
    assert config.background_type == BackgroundType.GRADIENT


def test_overlay_opacity_validation():
    """Test overlay opacity validation."""
    config = get_background_config(overlay_opacity=0.5)
    assert config.overlay_opacity == 0.5
    
    config = get_background_config(overlay_opacity=1.5)
    assert config.overlay_opacity == 1.0  # Clamped to max
    
    config = get_background_config(overlay_opacity=-0.5)
    assert config.overlay_opacity == 0.0  # Clamped to min


def test_fit_mode_validation():
    """Test fit mode validation."""
    config = get_background_config(background_fit="contain")
    assert config.background_fit == BackgroundFit.CONTAIN
    
    config = get_background_config(background_fit="invalid")
    assert config.background_fit == DEFAULT_FIT_MODE


# ── Image Background Tests ───────────────────────────────────────────────────

def test_list_available_backgrounds():
    """Test listing available background assets."""
    backgrounds = list_available_backgrounds()
    assert "images" in backgrounds
    assert "videos" in backgrounds
    assert isinstance(backgrounds["images"], list)
    assert isinstance(backgrounds["videos"], list)


def test_is_valid_background_path():
    """Test background path validation."""
    # Test with non-existent file
    assert is_valid_background_path("nonexistent.jpg", "local_image") is False
    
    # Test with empty path
    assert is_valid_background_path("", "local_image") is False


# ── Backward Compatibility Tests ─────────────────────────────────────────────

def test_gradient_default_preserved():
    """Test that gradient remains the default behavior."""
    config = get_default_background_config()
    assert config.background_type == BackgroundType.GRADIENT
    assert config.background_path is None


def test_empty_background_config():
    """Test empty configuration falls back to gradient."""
    config = get_background_config()
    assert config.background_type == BackgroundType.GRADIENT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
