"""
Visual Provider Abstraction Tests (Phase 3F.3).

Tests for the visual provider abstraction layer.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services.visual.visual_provider import (
    AIVisualProvider,
    FallbackVisualProvider,
    LocalVisualProvider,
    VisualAssetResult,
    VisualProviderFactory,
    VisualProviderType,
    generate_visual_asset,
)


class TestVisualAssetResult:
    """Test VisualAssetResult dataclass."""

    def test_visual_asset_result_creation(self):
        """Test creating a VisualAssetResult."""
        result = VisualAssetResult(
            asset_path="/path/to/image.jpg",
            asset_type="image",
            provider_name="local",
        )
        assert result.asset_path == "/path/to/image.jpg"
        assert result.asset_type == "image"
        assert result.provider_name == "local"
        assert result.metadata is None
        assert result.fallback_used is False
        assert result.error_message is None

    def test_is_fallback_with_none_path(self):
        """Test is_fallback returns True when asset_path is None."""
        result = VisualAssetResult(
            asset_path=None,
            asset_type="image",
            provider_name="fallback",
        )
        assert result.is_fallback is True

    def test_is_fallback_with_fallback_flag(self):
        """Test is_fallback returns True when fallback_used is True."""
        result = VisualAssetResult(
            asset_path="/path/to/image.jpg",
            asset_type="image",
            provider_name="local",
            fallback_used=True,
        )
        assert result.is_fallback is True

    def test_is_fallback_with_valid_asset(self):
        """Test is_fallback returns False for valid asset."""
        result = VisualAssetResult(
            asset_path="/path/to/image.jpg",
            asset_type="image",
            provider_name="local",
            fallback_used=False,
        )
        assert result.is_fallback is False


class TestFallbackVisualProvider:
    """Test FallbackVisualProvider."""

    def test_provider_name(self):
        """Test provider name."""
        provider = FallbackVisualProvider()
        assert provider.provider_name == "fallback"

    def test_generate_visual_always_fallback(self):
        """Test generate_visual always returns fallback result."""
        provider = FallbackVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"scene_number": 1},
        )
        assert result.asset_path is None
        assert result.asset_type == "image"
        assert result.provider_name == "fallback"
        assert result.fallback_used is True
        assert result.error_message is None

    def test_generate_visual_ignores_parameters(self):
        """Test generate_visual ignores all parameters."""
        provider = FallbackVisualProvider()
        result1 = provider.generate_visual(None, "16:9", None)
        result2 = provider.generate_visual("Prompt", "9:16", {"scene_number": 5})
        assert result1.is_fallback is True
        assert result2.is_fallback is True


class TestAIVisualProvider:
    """Test AIVisualProvider stub."""

    def test_provider_name(self):
        """Test provider name."""
        provider = AIVisualProvider()
        assert provider.provider_name == "ai"

    def test_generate_visual_not_implemented(self):
        """Test generate_visual returns not implemented error."""
        provider = AIVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"scene_number": 1},
        )
        assert result.asset_path is None
        assert result.asset_type == "image"
        assert result.provider_name == "ai"
        assert result.fallback_used is True
        assert result.error_message == "AI visual provider not implemented yet"


class TestLocalVisualProvider:
    """Test LocalVisualProvider."""

    def test_provider_name(self):
        """Test provider name."""
        provider = LocalVisualProvider()
        assert provider.provider_name == "local"

    def test_generate_visual_missing_job_id(self):
        """Test generate_visual returns fallback when job_id is missing."""
        provider = LocalVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"scene_number": 1},  # Missing job_id
        )
        assert result.asset_path is None
        assert result.fallback_used is True
        assert "Missing job_id" in result.error_message

    def test_generate_visual_missing_scene_context(self):
        """Test generate_visual returns fallback when scene_context is None."""
        provider = LocalVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context=None,
        )
        assert result.asset_path is None
        assert result.fallback_used is True
        assert "Missing job_id" in result.error_message

    @patch("backend.services.video.image_asset_discovery.select_auto_images")
    def test_generate_visual_success(self, mock_select):
        """Test generate_visual successfully selects an image."""
        mock_select.return_value = ["/path/to/image1.jpg", "/path/to/image2.jpg"]

        provider = LocalVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job-123", "scene_number": 0},
        )

        assert result.asset_path == "/path/to/image1.jpg"
        assert result.asset_type == "image"
        assert result.provider_name == "local"
        assert result.fallback_used is False
        assert result.metadata == {"job_id": "test-job-123", "scene_number": 0}

    @patch("backend.services.video.image_asset_discovery.select_auto_images")
    def test_generate_visual_scene_number_1(self, mock_select):
        """Test generate_visual with scene_number=1."""
        mock_select.return_value = ["/path/to/image1.jpg", "/path/to/image2.jpg"]

        provider = LocalVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job-123", "scene_number": 1},
        )

        assert result.asset_path == "/path/to/image2.jpg"
        assert result.fallback_used is False

    @patch("backend.services.video.image_asset_discovery.select_auto_images")
    def test_generate_visual_no_images_available(self, mock_select):
        """Test generate_visual when no images are available."""
        mock_select.return_value = [None, None]  # All None

        provider = LocalVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job-123", "scene_number": 0},
        )

        assert result.asset_path is None
        assert result.fallback_used is True
        assert result.error_message == "No valid images available"

    @patch("backend.services.video.image_asset_discovery.select_auto_images")
    def test_generate_visual_empty_selections(self, mock_select):
        """Test generate_visual when selections list is empty."""
        mock_select.return_value = []

        provider = LocalVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job-123", "scene_number": 0},
        )

        assert result.asset_path is None
        assert result.fallback_used is True

    @patch("backend.services.video.image_asset_discovery.select_auto_images")
    def test_generate_visual_16_9_aspect_ratio(self, mock_select):
        """Test generate_visual with 16:9 aspect ratio."""
        mock_select.return_value = ["/path/to/landscape.jpg"]

        provider = LocalVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job-123", "scene_number": 0},
        )

        mock_select.assert_called_once_with("test-job-123", 1, "16:9")
        assert result.asset_path == "/path/to/landscape.jpg"

    @patch("backend.services.video.image_asset_discovery.select_auto_images")
    def test_generate_visual_9_16_aspect_ratio(self, mock_select):
        """Test generate_visual with 9:16 aspect ratio."""
        mock_select.return_value = ["/path/to/portrait.jpg"]

        provider = LocalVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="9:16",
            scene_context={"job_id": "test-job-123", "scene_number": 0},
        )

        mock_select.assert_called_once_with("test-job-123", 1, "9:16")
        assert result.asset_path == "/path/to/portrait.jpg"

    @patch("backend.services.video.image_asset_discovery.select_auto_images")
    def test_generate_visual_exception_handling(self, mock_select):
        """Test generate_visual handles exceptions gracefully."""
        mock_select.side_effect = Exception("Test error")

        provider = LocalVisualProvider()
        result = provider.generate_visual(
            visual_prompt="Test prompt",
            aspect_ratio="16:9",
            scene_context={"job_id": "test-job-123", "scene_number": 0},
        )

        assert result.asset_path is None
        assert result.fallback_used is True
        assert "Test error" in result.error_message


class TestVisualProviderFactory:
    """Test VisualProviderFactory."""

    def test_create_local_provider(self):
        """Test creating local provider."""
        provider = VisualProviderFactory.create_provider("local")
        assert isinstance(provider, LocalVisualProvider)
        assert provider.provider_name == "local"

    def test_create_fallback_provider(self):
        """Test creating fallback provider."""
        provider = VisualProviderFactory.create_provider("fallback")
        assert isinstance(provider, FallbackVisualProvider)
        assert provider.provider_name == "fallback"

    def test_create_ai_provider(self):
        """Test creating AI provider."""
        provider = VisualProviderFactory.create_provider("ai")
        assert isinstance(provider, AIVisualProvider)
        assert provider.provider_name == "ai"

    def test_create_provider_case_insensitive(self):
        """Test provider type is case-insensitive."""
        provider1 = VisualProviderFactory.create_provider("LOCAL")
        provider2 = VisualProviderFactory.create_provider("Local")
        provider3 = VisualProviderFactory.create_provider("local")
        assert isinstance(provider1, LocalVisualProvider)
        assert isinstance(provider2, LocalVisualProvider)
        assert isinstance(provider3, LocalVisualProvider)

    def test_create_invalid_provider(self):
        """Test creating invalid provider raises ValueError."""
        with pytest.raises(ValueError, match="Invalid visual provider type"):
            VisualProviderFactory.create_provider("invalid")

    def test_get_default_provider_without_env(self):
        """Test get_default_provider returns local when env not set."""
        with patch.dict(os.environ, {}, clear=True):
            provider = VisualProviderFactory.get_default_provider()
            assert isinstance(provider, LocalVisualProvider)

    def test_get_default_provider_with_env_local(self):
        """Test get_default_provider respects VISUAL_PROVIDER=local."""
        with patch.dict(os.environ, {"VISUAL_PROVIDER": "local"}):
            provider = VisualProviderFactory.get_default_provider()
            assert isinstance(provider, LocalVisualProvider)

    def test_get_default_provider_with_env_fallback(self):
        """Test get_default_provider respects VISUAL_PROVIDER=fallback."""
        with patch.dict(os.environ, {"VISUAL_PROVIDER": "fallback"}):
            provider = VisualProviderFactory.get_default_provider()
            assert isinstance(provider, FallbackVisualProvider)

    def test_get_default_provider_with_env_ai(self):
        """Test get_default_provider respects VISUAL_PROVIDER=ai."""
        with patch.dict(os.environ, {"VISUAL_PROVIDER": "ai"}):
            provider = VisualProviderFactory.get_default_provider()
            assert isinstance(provider, AIVisualProvider)


class TestGenerateVisualAsset:
    """Test generate_visual_asset convenience function."""

    @patch("backend.services.visual.visual_provider.VisualProviderFactory.get_default_provider")
    def test_generate_visual_asset_default_provider(self, mock_get_default):
        """Test generate_visual_asset uses default provider."""
        mock_provider = MagicMock()
        mock_provider.generate_visual.return_value = VisualAssetResult(
            asset_path="/test.jpg",
            asset_type="image",
            provider_name="test",
        )
        mock_get_default.return_value = mock_provider

        result = generate_visual_asset(
            visual_prompt="Test",
            aspect_ratio="16:9",
            scene_context={"job_id": "test", "scene_number": 0},
        )

        mock_provider.generate_visual.assert_called_once()
        assert result.asset_path == "/test.jpg"

    @patch("backend.services.visual.visual_provider.VisualProviderFactory.create_provider")
    def test_generate_visual_asset_override_provider(self, mock_create):
        """Test generate_visual_asset with provider override."""
        mock_provider = MagicMock()
        mock_provider.generate_visual.return_value = VisualAssetResult(
            asset_path="/test.jpg",
            asset_type="image",
            provider_name="test",
        )
        mock_create.return_value = mock_provider

        result = generate_visual_asset(
            visual_prompt="Test",
            aspect_ratio="16:9",
            scene_context={"job_id": "test", "scene_number": 0},
            provider_type="fallback",
        )

        mock_create.assert_called_once_with("fallback")
        mock_provider.generate_visual.assert_called_once()
        assert result.asset_path == "/test.jpg"

    @patch("backend.services.visual.visual_provider.VisualProviderFactory.get_default_provider")
    def test_generate_visual_asset_missing_visual_prompt(self, mock_get_default):
        """Test generate_visual_asset with None visual_prompt."""
        mock_provider = MagicMock()
        mock_provider.generate_visual.return_value = VisualAssetResult(
            asset_path=None,
            asset_type="image",
            provider_name="fallback",
            fallback_used=True,
        )
        mock_get_default.return_value = mock_provider

        result = generate_visual_asset(
            visual_prompt=None,
            aspect_ratio="16:9",
            scene_context=None,
        )

        mock_provider.generate_visual.assert_called_once_with(None, "16:9", None)
        assert result.is_fallback is True


class TestBackwardCompatibility:
    """Test backward compatibility with existing background modes."""

    def test_fallback_provider_compatible_with_gradient(self):
        """Test fallback provider is compatible with gradient background."""
        provider = FallbackVisualProvider()
        result = provider.generate_visual(None, "16:9", None)
        assert result.is_fallback is True
        # This result should be interpreted as "use gradient" by the renderer

    @patch("backend.services.video.image_asset_discovery.select_auto_images")
    def test_local_provider_compatible_with_local_image_auto(self, mock_select):
        """Test local provider is compatible with local_image_auto behavior."""
        mock_select.return_value = ["/path/to/image.jpg"]
        provider = LocalVisualProvider()
        result = provider.generate_visual(
            None,
            "16:9",
            {"job_id": "test", "scene_number": 0},
        )
        assert result.asset_path == "/path/to/image.jpg"
        # This result should be compatible with existing local_image rendering

    def test_missing_visual_prompt_does_not_crash(self):
        """Test that None/empty visual_prompt does not crash providers."""
        providers = [
            LocalVisualProvider(),
            FallbackVisualProvider(),
            AIVisualProvider(),
        ]

        for provider in providers:
            result = provider.generate_visual(
                visual_prompt=None,
                aspect_ratio="16:9",
                scene_context={"job_id": "test", "scene_number": 0},
            )
            # All providers should handle None visual_prompt gracefully
            assert result is not None
            assert isinstance(result, VisualAssetResult)


class TestVisualProviderType:
    """Test VisualProviderType enum."""

    def test_enum_values(self):
        """Test enum values are correct."""
        assert VisualProviderType.LOCAL.value == "local"
        assert VisualProviderType.FALLBACK.value == "fallback"
        assert VisualProviderType.AI.value == "ai"

    def test_enum_comparison(self):
        """Test enum comparison works."""
        assert VisualProviderType.LOCAL == "local"
        assert VisualProviderType.FALLBACK == "fallback"
        assert VisualProviderType.AI == "ai"
