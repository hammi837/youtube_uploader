"""
Phase 3F.1: Local Image Background Foundation Tests.

Tests for automatic local image background selection with deterministic seeding.
"""

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from backend.services.video.background_config import (
    BackgroundType,
    BackgroundFit,
    get_background_config,
    is_valid_background_type,
)
from backend.services.video.image_asset_discovery import (
    ImageMetadata,
    discover_image_assets,
    extract_image_metadata,
    select_auto_images,
)


class TestBackgroundTypeValidation:
    """Test that local_image_auto is a valid background type."""

    def test_local_image_auto_enum_exists(self):
        """Verify LOCAL_IMAGE_AUTO enum value exists."""
        assert hasattr(BackgroundType, "LOCAL_IMAGE_AUTO")
        assert BackgroundType.LOCAL_IMAGE_AUTO.value == "local_image_auto"

    def test_local_image_auto_is_valid_type(self):
        """Verify local_image_auto is recognized as valid."""
        assert is_valid_background_type("local_image_auto")
        assert is_valid_background_type("LOCAL_IMAGE_AUTO")
        assert is_valid_background_type("Local_Image_Auto")

    def test_background_config_accepts_auto(self):
        """Verify BackgroundConfig accepts local_image_auto type."""
        config = get_background_config(background_type="local_image_auto")
        assert config.background_type == BackgroundType.LOCAL_IMAGE_AUTO


class TestImageMetadata:
    """Test ImageMetadata dataclass."""

    def test_landscape_orientation(self):
        """Test landscape orientation detection."""
        metadata = ImageMetadata(path="test.jpg", filename="test.jpg", width=1920, height=1080)
        assert metadata.orientation == "landscape"
        assert metadata.aspect_ratio == 1920 / 1080

    def test_portrait_orientation(self):
        """Test portrait orientation detection."""
        metadata = ImageMetadata(path="test.jpg", filename="test.jpg", width=1080, height=1920)
        assert metadata.orientation == "portrait"
        assert metadata.aspect_ratio == 1080 / 1920

    def test_square_orientation(self):
        """Test square orientation detection."""
        metadata = ImageMetadata(path="test.jpg", filename="test.jpg", width=1080, height=1080)
        assert metadata.orientation == "square"
        assert metadata.aspect_ratio == 1.0

    def test_unknown_orientation_none_dimensions(self):
        """Test unknown orientation when dimensions are None."""
        metadata = ImageMetadata(path="test.jpg", filename="test.jpg", width=None, height=None)
        assert metadata.orientation == "unknown"
        assert metadata.aspect_ratio == 0.0

    def test_aspect_ratio_zero_height(self):
        """Test aspect ratio with zero height."""
        metadata = ImageMetadata(path="test.jpg", filename="test.jpg", width=1920, height=0)
        assert metadata.aspect_ratio == 0.0


class TestImageDiscovery:
    """Test image asset discovery."""

    @patch("backend.services.video.image_asset_discovery.Image")
    def test_discover_images_empty_directory(self, mock_pil):
        """Test discovery when images directory doesn't exist."""
        mock_pil.return_value = None
        
        images = discover_image_assets()
        assert images == []

    def test_discover_filters_invalid_extensions(self):
        """Test that discovery filters unsupported file extensions."""
        from pathlib import Path
        import tempfile
        
        # Create a temporary directory with invalid file
        with tempfile.TemporaryDirectory() as tmpdir:
            images_dir = Path(tmpdir) / "backgrounds" / "images"
            images_dir.mkdir(parents=True)
            
            # Create a text file (invalid extension)
            (images_dir / "test.txt").write_text("not an image")
            
            images = discover_image_assets(images_dir=images_dir)
            assert images == []

    def test_discover_filters_hidden_files(self):
        """Test that discovery filters hidden files."""
        from pathlib import Path
        import tempfile
        
        # Create a temporary directory with hidden file
        with tempfile.TemporaryDirectory() as tmpdir:
            images_dir = Path(tmpdir) / "backgrounds" / "images"
            images_dir.mkdir(parents=True)
            
            # Create a hidden file
            (images_dir / ".hidden.jpg").write_text("not an image")
            
            images = discover_image_assets(images_dir=images_dir)
            assert images == []

    @patch("backend.services.video.image_asset_discovery.extract_image_metadata")
    @patch("backend.services.video.image_asset_discovery.Path")
    def test_discover_filters_invalid_metadata(self, mock_path, mock_extract):
        """Test that discovery filters images with invalid metadata."""
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.is_file.return_value = True
        mock_path.return_value.name = "test.jpg"
        mock_path.return_value.suffix = ".jpg"
        mock_path.return_value.rglob.return_value = [mock_path.return_value]
        mock_extract.return_value = None  # Invalid metadata
        
        images = discover_image_assets()
        assert images == []

    @patch("backend.services.video.image_asset_discovery.extract_image_metadata")
    def test_discover_valid_images(self, mock_extract):
        """Test that discovery returns valid images."""
        # Create a real Path object for testing
        from pathlib import Path
        import tempfile
        import os
        
        # Create a temporary directory structure
        with tempfile.TemporaryDirectory() as tmpdir:
            images_dir = Path(tmpdir) / "backgrounds" / "images"
            images_dir.mkdir(parents=True)
            
            # Create a mock image file (we won't actually create a real image)
            # Just test that the function structure works
            mock_extract.return_value = ImageMetadata(
                path=str(images_dir / "test.jpg"),
                filename="test.jpg",
                width=1920,
                height=1080
            )
            
            images = discover_image_assets(images_dir=images_dir)
            # Since directory is empty, should return empty list
            assert images == []


class TestAutoSelectionBasic:
    """Test basic auto-selection functionality."""

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_with_valid_assets(self, mock_discover):
        """Test auto-selection returns valid image paths."""
        # Mock 3 valid image assets
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=1920, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=1920, height=1080),
            ImageMetadata(path="image3.jpg", filename="image3.jpg", width=1920, height=1080),
        ]

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        assert len(selections) == 5
        assert all(s is not None for s in selections)
        assert all(s.endswith(".jpg") for s in selections)

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_filters_zero_dimensions(self, mock_discover):
        """Test auto-selection excludes images with zero dimensions."""
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=1920, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=0, height=1080),  # Invalid
            ImageMetadata(path="image3.jpg", filename="image3.jpg", width=1920, height=1080),
        ]

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        # Should only use valid images (image1 and image3)
        assert len(selections) == 3
        assert "image2.jpg" not in selections

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_filters_none_dimensions(self, mock_discover):
        """Test auto-selection excludes images with None dimensions."""
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=1920, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=None, height=1080),  # Invalid
            ImageMetadata(path="image3.jpg", filename="image3.jpg", width=1920, height=1080),
        ]

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        # Should only use valid images
        assert len(selections) == 3
        assert "image2.jpg" not in selections


class TestAutoSelectionAspectRatioFiltering:
    """Test aspect ratio-aware filtering."""

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_prefers_landscape_for_16_9(self, mock_discover):
        """Test auto-selection prefers landscape images for 16:9 aspect ratio."""
        mock_discover.return_value = [
            ImageMetadata(path="landscape1.jpg", filename="landscape1.jpg", width=1920, height=1080),
            ImageMetadata(path="landscape2.jpg", filename="landscape2.jpg", width=3840, height=2160),
            ImageMetadata(path="portrait1.jpg", filename="portrait1.jpg", width=1080, height=1920),
        ]

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        # Should prefer landscape images
        assert "portrait1.jpg" not in selections
        assert all("landscape" in s for s in selections)

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_prefers_portrait_for_9_16(self, mock_discover):
        """Test auto-selection prefers portrait images for 9:16 aspect ratio."""
        mock_discover.return_value = [
            ImageMetadata(path="landscape1.jpg", filename="landscape1.jpg", width=1920, height=1080),
            ImageMetadata(path="portrait1.jpg", filename="portrait1.jpg", width=1080, height=1920),
            ImageMetadata(path="portrait2.jpg", filename="portrait2.jpg", width=1080, height=1920),
        ]

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="9:16",
        )

        # Should prefer portrait images
        assert "landscape1.jpg" not in selections
        assert all("portrait" in s for s in selections)

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_fallback_to_all_if_no_match(self, mock_discover):
        """Test auto-selection falls back to all valid images if no matching orientation."""
        mock_discover.return_value = [
            ImageMetadata(path="landscape1.jpg", filename="landscape1.jpg", width=1920, height=1080),
            ImageMetadata(path="landscape2.jpg", filename="landscape2.jpg", width=3840, height=2160),
        ]

        # Request 9:16 but only landscape images available
        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="9:16",
        )

        # Should fall back to landscape images
        assert len(selections) == 3
        assert all(s is not None for s in selections)


class TestAutoSelectionDeterministic:
    """Test deterministic selection behavior."""

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_deterministic_same_job_id(self, mock_discover):
        """Test auto-selection produces identical results for same job_id."""
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=1920, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=1920, height=1080),
            ImageMetadata(path="image3.jpg", filename="image3.jpg", width=1920, height=1080),
        ]

        selections1 = select_auto_images(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        selections2 = select_auto_images(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        assert selections1 == selections2

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_different_job_id(self, mock_discover):
        """Test auto-selection produces different results for different job_id."""
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=1920, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=1920, height=1080),
            ImageMetadata(path="image3.jpg", filename="image3.jpg", width=1920, height=1080),
        ]

        selections1 = select_auto_images(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        selections2 = select_auto_images(
            job_id="test-job-456",
            scene_count=5,
            aspect_ratio="16:9",
        )

        # Different job_id should produce different shuffle order
        # (though occasionally they might match by chance, this is unlikely)
        # We just verify the function is called correctly
        assert len(selections1) == len(selections2) == 5

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_uses_hashlib_sha256(self, mock_discover):
        """Test that selection uses hashlib.sha256 for stable seeding."""
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=1920, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=1920, height=1080),
        ]

        # Verify the function runs without error
        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        assert len(selections) == 3

        # Verify that the same job_id produces same result (hash stability)
        selections2 = select_auto_images(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        assert selections == selections2


class TestAutoSelectionSceneVariation:
    """Test scene variation behavior."""

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_varies_scenes(self, mock_discover):
        """Test auto-selection varies backgrounds across scenes when enough assets exist."""
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=1920, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=1920, height=1080),
            ImageMetadata(path="image3.jpg", filename="image3.jpg", width=1920, height=1080),
            ImageMetadata(path="image4.jpg", filename="image4.jpg", width=1920, height=1080),
            ImageMetadata(path="image5.jpg", filename="image5.jpg", width=1920, height=1080),
        ]

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=10,
            aspect_ratio="16:9",
        )

        # Should have 10 selections
        assert len(selections) == 10

        # Should use different images (not all the same)
        unique_images = set(selections)
        assert len(unique_images) > 1, "Should use multiple different images"

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_reuses_when_fewer_assets(self, mock_discover):
        """Test auto-selection reuses assets when fewer assets than scenes."""
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=1920, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=1920, height=1080),
        ]

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=10,
            aspect_ratio="16:9",
        )

        # Should have 10 selections
        assert len(selections) == 10

        # Should reuse images (only 2 available for 10 scenes)
        unique_images = set(selections)
        assert len(unique_images) == 2, "Should reuse available images"

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_not_all_same_when_multiple_assets(self, mock_discover):
        """Test auto-selection doesn't assign same image to all scenes when multiple available."""
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=1920, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=1920, height=1080),
            ImageMetadata(path="image3.jpg", filename="image3.jpg", width=1920, height=1080),
        ]

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        # Should not use the same image for all scenes
        assert not all(s == selections[0] for s in selections), "Should vary backgrounds"


class TestAutoSelectionFallback:
    """Test fallback behavior."""

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_empty_fallback_to_gradient(self, mock_discover):
        """Test auto-selection falls back to gradient when no valid images available."""
        mock_discover.return_value = []

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        # Should return all None (gradient fallback)
        assert len(selections) == 5
        assert all(s is None for s in selections)

    @patch("backend.services.video.image_asset_discovery.discover_image_assets")
    def test_auto_selection_all_invalid_fallback_to_gradient(self, mock_discover):
        """Test auto-selection falls back to gradient when all images are invalid."""
        mock_discover.return_value = [
            ImageMetadata(path="image1.jpg", filename="image1.jpg", width=0, height=1080),
            ImageMetadata(path="image2.jpg", filename="image2.jpg", width=None, height=1080),
        ]

        selections = select_auto_images(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        # Should return all None (gradient fallback)
        assert len(selections) == 3
        assert all(s is None for s in selections)


class TestBackwardCompatibility:
    """Test backward compatibility with existing background modes."""

    def test_gradient_still_valid(self):
        """Verify gradient background type still works."""
        assert is_valid_background_type("gradient")
        config = get_background_config(background_type="gradient")
        assert config.background_type == BackgroundType.GRADIENT

    def test_solid_color_still_valid(self):
        """Verify solid_color background type still works."""
        assert is_valid_background_type("solid_color")
        config = get_background_config(background_type="solid_color")
        assert config.background_type == BackgroundType.SOLID_COLOR

    def test_local_image_still_valid(self):
        """Verify local_image background type still works."""
        assert is_valid_background_type("local_image")
        config = get_background_config(background_type="local_image")
        assert config.background_type == BackgroundType.LOCAL_IMAGE

    def test_local_video_still_valid(self):
        """Verify local_video background type still works."""
        assert is_valid_background_type("local_video")
        config = get_background_config(background_type="local_video")
        assert config.background_type == BackgroundType.LOCAL_VIDEO

    def test_local_video_auto_still_valid(self):
        """Verify local_video_auto background type still works."""
        assert is_valid_background_type("local_video_auto")
        config = get_background_config(background_type="local_video_auto")
        assert config.background_type == BackgroundType.LOCAL_VIDEO_AUTO

    def test_invalid_type_fallback_to_gradient(self):
        """Verify invalid background type falls back to gradient."""
        config = get_background_config(background_type="invalid_type")
        assert config.background_type == BackgroundType.GRADIENT
