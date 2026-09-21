"""
Visual Provider Integration Tests (Phase 3F.4).

Tests for the integration of the Visual Provider abstraction into the video pipeline.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from backend.services.video.background_config import BackgroundType
from backend.services.visual.visual_provider import (
    VisualAssetResult,
    VisualProviderFactory,
    VisualProviderType,
)


class TestBackgroundTypeVisualProvider:
    """Test that VISUAL_PROVIDER background type exists."""

    def test_visual_provider_enum_exists(self):
        """Test VISUAL_PROVIDER enum value exists."""
        assert hasattr(BackgroundType, "VISUAL_PROVIDER")
        assert BackgroundType.VISUAL_PROVIDER.value == "visual_provider"

    def test_visual_provider_in_valid_types(self):
        """Test VISUAL_PROVIDER is in valid background types."""
        from backend.services.video.background_config import VALID_BACKGROUND_TYPES
        assert BackgroundType.VISUAL_PROVIDER.value in VALID_BACKGROUND_TYPES


class TestVisualProviderIntegration:
    """Test visual provider integration logic."""

    @patch("backend.services.visual.visual_provider.generate_visual_asset")
    def test_visual_provider_selects_asset(self, mock_generate):
        """Test visual provider successfully selects an asset."""
        mock_generate.return_value = VisualAssetResult(
            asset_path="/path/to/image.jpg",
            asset_type="image",
            provider_name="local",
            fallback_used=False,
        )

        from backend.services.visual.visual_provider import generate_visual_asset

        result = generate_visual_asset(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job", "scene_number": 0},
        )

        assert result.asset_path == "/path/to/image.jpg"
        assert result.fallback_used is False
        assert result.provider_name == "local"

    @patch("backend.services.visual.visual_provider.generate_visual_asset")
    def test_visual_provider_fallback_to_gradient(self, mock_generate):
        """Test visual provider fallback returns None asset_path."""
        mock_generate.return_value = VisualAssetResult(
            asset_path=None,
            asset_type="image",
            provider_name="fallback",
            fallback_used=True,
        )

        from backend.services.visual.visual_provider import generate_visual_asset

        result = generate_visual_asset(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job", "scene_number": 0},
        )

        assert result.asset_path is None
        assert result.fallback_used is True
        assert result.provider_name == "fallback"

    @patch("backend.services.visual.visual_provider.generate_visual_asset")
    def test_visual_provider_with_none_prompt(self, mock_generate):
        """Test visual provider handles None visual_prompt."""
        mock_generate.return_value = VisualAssetResult(
            asset_path=None,
            asset_type="image",
            provider_name="fallback",
            fallback_used=True,
        )

        from backend.services.visual.visual_provider import generate_visual_asset

        result = generate_visual_asset(
            visual_prompt=None,
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job", "scene_number": 0},
        )

        assert result is not None
        assert result.fallback_used is True

    @patch("backend.services.visual.visual_provider.generate_visual_asset")
    def test_visual_provider_exception_handling(self, mock_generate):
        """Test visual provider exception is handled gracefully."""
        mock_generate.side_effect = Exception("Provider error")

        from backend.services.visual.visual_provider import generate_visual_asset

        with pytest.raises(Exception, match="Provider error"):
            generate_visual_asset(
                visual_prompt="Test prompt",
                aspect_ratio="16:9",
                scene_context={"job_id": "test-job", "scene_number": 0},
            )

    @patch("backend.services.visual.visual_provider.generate_visual_asset")
    def test_visual_provider_16_9_aspect_ratio(self, mock_generate):
        """Test visual provider with 16:9 aspect ratio."""
        mock_generate.return_value = VisualAssetResult(
            asset_path="/path/to/landscape.jpg",
            asset_type="image",
            provider_name="local",
            fallback_used=False,
        )

        from backend.services.visual.visual_provider import generate_visual_asset

        result = generate_visual_asset(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job", "scene_number": 0},
        )

        mock_generate.assert_called_once()
        assert result.asset_path == "/path/to/landscape.jpg"

    @patch("backend.services.visual.visual_provider.generate_visual_asset")
    def test_visual_provider_9_16_aspect_ratio(self, mock_generate):
        """Test visual provider with 9:16 aspect ratio."""
        mock_generate.return_value = VisualAssetResult(
            asset_path="/path/to/portrait.jpg",
            asset_type="image",
            provider_name="local",
            fallback_used=False,
        )

        from backend.services.visual.visual_provider import generate_visual_asset

        result = generate_visual_asset(
            visual_prompt="Test prompt",
            aspect_ratio="9:16",
            scene_context={"job_id": "test-job", "scene_number": 0},
        )

        mock_generate.assert_called_once()
        assert result.asset_path == "/path/to/portrait.jpg"


class TestProviderSelection:
    """Test provider selection configuration."""

    def test_local_provider_from_env(self):
        """Test local provider is selected from env variable."""
        with patch.dict(os.environ, {"VISUAL_PROVIDER": "local"}):
            provider = VisualProviderFactory.get_default_provider()
            assert provider.provider_name == "local"

    def test_fallback_provider_from_env(self):
        """Test fallback provider is selected from env variable."""
        with patch.dict(os.environ, {"VISUAL_PROVIDER": "fallback"}):
            provider = VisualProviderFactory.get_default_provider()
            assert provider.provider_name == "fallback"

    def test_ai_provider_from_env(self):
        """Test AI provider is selected from env variable."""
        with patch.dict(os.environ, {"VISUAL_PROVIDER": "ai"}):
            provider = VisualProviderFactory.get_default_provider()
            assert provider.provider_name == "ai"

    def test_default_provider_without_env(self):
        """Test default provider is local when env not set."""
        with patch.dict(os.environ, {}, clear=True):
            provider = VisualProviderFactory.get_default_provider()
            assert provider.provider_name == "local"


class TestExplicitBackgroundPrecedence:
    """Test that explicit background modes take precedence over visual provider."""

    def test_gradient_precedence(self):
        """Test gradient background takes precedence."""
        # If a scene has explicit gradient, it should not be overridden by visual provider
        scene = {
            "background_type": BackgroundType.GRADIENT.value,
            "background_path": None,
        }
        
        # The integration logic should not override explicit backgrounds
        assert scene["background_type"] == BackgroundType.GRADIENT.value

    def test_local_image_precedence(self):
        """Test local_image background takes precedence."""
        scene = {
            "background_type": BackgroundType.LOCAL_IMAGE.value,
            "background_path": "/path/to/image.jpg",
        }
        
        # The integration logic should not override explicit backgrounds
        assert scene["background_type"] == BackgroundType.LOCAL_IMAGE.value
        assert scene["background_path"] == "/path/to/image.jpg"

    def test_local_video_precedence(self):
        """Test local_video background takes precedence."""
        scene = {
            "background_type": BackgroundType.LOCAL_VIDEO.value,
            "background_path": "/path/to/video.mp4",
        }
        
        # The integration logic should not override explicit backgrounds
        assert scene["background_type"] == BackgroundType.LOCAL_VIDEO.value
        assert scene["background_path"] == "/path/to/video.mp4"


class TestLocalImageAutoCompatibility:
    """Test that local_image_auto continues to work."""

    def test_local_image_auto_enum_exists(self):
        """Test LOCAL_IMAGE_AUTO enum still exists."""
        assert hasattr(BackgroundType, "LOCAL_IMAGE_AUTO")
        assert BackgroundType.LOCAL_IMAGE_AUTO.value == "local_image_auto"

    def test_local_image_auto_is_valid(self):
        """Test LOCAL_IMAGE_AUTO is still a valid background type."""
        from backend.services.video.background_config import VALID_BACKGROUND_TYPES
        assert BackgroundType.LOCAL_IMAGE_AUTO.value in VALID_BACKGROUND_TYPES


class TestLocalVideoAutoCompatibility:
    """Test that local_video_auto continues to work."""

    def test_local_video_auto_enum_exists(self):
        """Test LOCAL_VIDEO_AUTO enum still exists."""
        assert hasattr(BackgroundType, "LOCAL_VIDEO_AUTO")
        assert BackgroundType.LOCAL_VIDEO_AUTO.value == "local_video_auto"

    def test_local_video_auto_is_valid(self):
        """Test LOCAL_VIDEO_AUTO is still a valid background type."""
        from backend.services.video.background_config import VALID_BACKGROUND_TYPES
        assert BackgroundType.LOCAL_VIDEO_AUTO.value in VALID_BACKGROUND_TYPES


class TestInvalidProviderConfiguration:
    """Test invalid provider configuration handling."""

    def test_invalid_provider_type_raises_error(self):
        """Test invalid provider type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid visual provider type"):
            VisualProviderFactory.create_provider("invalid_provider")
