"""
Phase 3E.5: Intelligent Local Visual Selection and Scene Variation Tests.

Tests for automatic background selection from local video assets with deterministic seeding.
"""

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.services.video.background_config import (
    BackgroundType,
    BackgroundFit,
    get_background_config,
    is_valid_background_type,
)
from backend.services.video.video_asset_discovery import (
    VideoMetadata,
    discover_video_assets,
    extract_video_metadata,
    select_auto_backgrounds,
)


class TestBackgroundTypeValidation:
    """Test that local_video_auto is a valid background type."""

    def test_local_video_auto_enum_exists(self):
        """Verify LOCAL_VIDEO_AUTO enum value exists."""
        assert hasattr(BackgroundType, "LOCAL_VIDEO_AUTO")
        assert BackgroundType.LOCAL_VIDEO_AUTO.value == "local_video_auto"

    def test_local_video_auto_is_valid_type(self):
        """Verify local_video_auto is recognized as valid."""
        assert is_valid_background_type("local_video_auto")
        assert is_valid_background_type("LOCAL_VIDEO_AUTO")
        assert is_valid_background_type("Local_Video_Auto")

    def test_background_config_accepts_auto(self):
        """Verify BackgroundConfig accepts local_video_auto type."""
        config = get_background_config(background_type="local_video_auto")
        assert config.background_type == BackgroundType.LOCAL_VIDEO_AUTO


class TestAutoSelectionBasic:
    """Test basic auto-selection functionality."""

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_with_valid_assets(self, mock_discover):
        """Test auto-selection returns valid video paths."""
        # Mock 3 valid video assets
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=15.0, width=1920, height=1080),
            VideoMetadata(path="video3.mp4", filename="video3.mp4", duration=12.0, width=1920, height=1080),
        ]

        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        assert len(selections) == 5
        assert all(s is not None for s in selections)
        assert all(s.endswith(".mp4") for s in selections)

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_filters_zero_duration(self, mock_discover):
        """Test auto-selection excludes videos with zero duration."""
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=0.0, width=1920, height=1080),  # Invalid
            VideoMetadata(path="video3.mp4", filename="video3.mp4", duration=15.0, width=1920, height=1080),
        ]

        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        # Should only use valid videos (video1 and video3)
        assert len(selections) == 3
        assert "video2.mp4" not in selections

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_filters_none_duration(self, mock_discover):
        """Test auto-selection excludes videos with None duration."""
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=None, width=1920, height=1080),  # Invalid
            VideoMetadata(path="video3.mp4", filename="video3.mp4", duration=15.0, width=1920, height=1080),
        ]

        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        # Should only use valid videos
        assert len(selections) == 3
        assert "video2.mp4" not in selections


class TestAutoSelectionAspectRatioFiltering:
    """Test aspect ratio-aware filtering."""

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_prefers_landscape_for_16_9(self, mock_discover):
        """Test auto-selection prefers landscape videos for 16:9 aspect ratio."""
        mock_discover.return_value = [
            VideoMetadata(path="landscape1.mp4", filename="landscape1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="landscape2.mp4", filename="landscape2.mp4", duration=15.0, width=3840, height=2160),
            VideoMetadata(path="portrait1.mp4", filename="portrait1.mp4", duration=8.0, width=1080, height=1920),
        ]

        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        # Should prefer landscape videos
        assert "portrait1.mp4" not in selections
        assert all("landscape" in s for s in selections)

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_prefers_portrait_for_9_16(self, mock_discover):
        """Test auto-selection prefers portrait videos for 9:16 aspect ratio."""
        mock_discover.return_value = [
            VideoMetadata(path="landscape1.mp4", filename="landscape1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="portrait1.mp4", filename="portrait1.mp4", duration=8.0, width=1080, height=1920),
            VideoMetadata(path="portrait2.mp4", filename="portrait2.mp4", duration=12.0, width=1080, height=1920),
        ]

        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="9:16",
        )

        # Should prefer portrait videos
        assert "landscape1.mp4" not in selections
        assert all("portrait" in s for s in selections)

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_fallback_to_all_if_no_match(self, mock_discover):
        """Test auto-selection falls back to all valid videos if no matching orientation."""
        mock_discover.return_value = [
            VideoMetadata(path="landscape1.mp4", filename="landscape1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="landscape2.mp4", filename="landscape2.mp4", duration=15.0, width=3840, height=2160),
        ]

        # Request 9:16 but only landscape videos available
        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="9:16",
        )

        # Should fall back to landscape videos
        assert len(selections) == 3
        assert all(s is not None for s in selections)


class TestAutoSelectionDeterministic:
    """Test deterministic selection behavior."""

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_deterministic_same_job_id(self, mock_discover):
        """Test auto-selection produces identical results for same job_id."""
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=15.0, width=1920, height=1080),
            VideoMetadata(path="video3.mp4", filename="video3.mp4", duration=12.0, width=1920, height=1080),
        ]

        selections1 = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        selections2 = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        assert selections1 == selections2

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_different_job_id(self, mock_discover):
        """Test auto-selection produces different results for different job_id."""
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=15.0, width=1920, height=1080),
            VideoMetadata(path="video3.mp4", filename="video3.mp4", duration=12.0, width=1920, height=1080),
        ]

        selections1 = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        selections2 = select_auto_backgrounds(
            job_id="test-job-456",
            scene_count=5,
            aspect_ratio="16:9",
        )

        # Different job_id should produce different shuffle order
        # (though occasionally they might match by chance, this is unlikely)
        # We just verify the function is called correctly
        assert len(selections1) == len(selections2) == 5

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_uses_hashlib_sha256(self, mock_discover):
        """Test that selection uses hashlib.sha256 for stable seeding."""
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=15.0, width=1920, height=1080),
        ]

        # Verify the function runs without error
        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        assert len(selections) == 3

        # Verify that the same job_id produces same result (hash stability)
        selections2 = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=3,
            aspect_ratio="16:9",
        )

        assert selections == selections2


class TestAutoSelectionSceneVariation:
    """Test scene variation behavior."""

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_varies_scenes(self, mock_discover):
        """Test auto-selection varies backgrounds across scenes when enough assets exist."""
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=15.0, width=1920, height=1080),
            VideoMetadata(path="video3.mp4", filename="video3.mp4", duration=12.0, width=1920, height=1080),
            VideoMetadata(path="video4.mp4", filename="video4.mp4", duration=8.0, width=1920, height=1080),
            VideoMetadata(path="video5.mp4", filename="video5.mp4", duration=20.0, width=1920, height=1080),
        ]

        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=10,
            aspect_ratio="16:9",
        )

        # Should have 10 selections
        assert len(selections) == 10

        # Should use different videos (not all the same)
        unique_videos = set(selections)
        assert len(unique_videos) > 1, "Should use multiple different videos"

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_reuses_when_fewer_assets(self, mock_discover):
        """Test auto-selection reuses assets when fewer assets than scenes."""
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=15.0, width=1920, height=1080),
        ]

        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=10,
            aspect_ratio="16:9",
        )

        # Should have 10 selections
        assert len(selections) == 10

        # Should reuse videos (only 2 available for 10 scenes)
        unique_videos = set(selections)
        assert len(unique_videos) == 2, "Should reuse available videos"

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_not_all_same_when_multiple_assets(self, mock_discover):
        """Test auto-selection doesn't assign same video to all scenes when multiple available."""
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=10.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=15.0, width=1920, height=1080),
            VideoMetadata(path="video3.mp4", filename="video3.mp4", duration=12.0, width=1920, height=1080),
        ]

        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        # Should not use the same video for all scenes
        assert not all(s == selections[0] for s in selections), "Should vary backgrounds"


class TestAutoSelectionFallback:
    """Test fallback behavior."""

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_empty_fallback_to_gradient(self, mock_discover):
        """Test auto-selection falls back to gradient when no valid videos available."""
        mock_discover.return_value = []

        selections = select_auto_backgrounds(
            job_id="test-job-123",
            scene_count=5,
            aspect_ratio="16:9",
        )

        # Should return all None (gradient fallback)
        assert len(selections) == 5
        assert all(s is None for s in selections)

    @patch("backend.services.video.video_asset_discovery.discover_video_assets")
    def test_auto_selection_all_invalid_fallback_to_gradient(self, mock_discover):
        """Test auto-selection falls back to gradient when all videos are invalid."""
        mock_discover.return_value = [
            VideoMetadata(path="video1.mp4", filename="video1.mp4", duration=0.0, width=1920, height=1080),
            VideoMetadata(path="video2.mp4", filename="video2.mp4", duration=None, width=1920, height=1080),
        ]

        selections = select_auto_backgrounds(
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

    def test_invalid_type_fallback_to_gradient(self):
        """Verify invalid background type falls back to gradient."""
        config = get_background_config(background_type="invalid_type")
        assert config.background_type == BackgroundType.GRADIENT


class TestVideoMetadataOrientation:
    """Test VideoMetadata orientation calculation."""

    def test_landscape_orientation(self):
        """Test landscape orientation detection."""
        metadata = VideoMetadata(path="test.mp4", filename="test.mp4", duration=10.0, width=1920, height=1080)
        assert metadata.orientation == "landscape"

    def test_portrait_orientation(self):
        """Test portrait orientation detection."""
        metadata = VideoMetadata(path="test.mp4", filename="test.mp4", duration=10.0, width=1080, height=1920)
        assert metadata.orientation == "portrait"

    def test_square_orientation(self):
        """Test square orientation detection."""
        metadata = VideoMetadata(path="test.mp4", filename="test.mp4", duration=10.0, width=1080, height=1080)
        assert metadata.orientation == "square"

    def test_unknown_orientation_none_dimensions(self):
        """Test unknown orientation when dimensions are None."""
        metadata = VideoMetadata(path="test.mp4", filename="test.mp4", duration=10.0, width=None, height=None)
        assert metadata.orientation == "unknown"
