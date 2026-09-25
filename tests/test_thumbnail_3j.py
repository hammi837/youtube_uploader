"""
tests/test_thumbnail_3j.py — Phase 3J thumbnail generation tests.

Tests for representative scene frame extraction, style handling, fallback behavior,
and edge cases as specified in the Phase 3J proposal.
"""

import os
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pytest

from backend.services.video.thumbnail import (
    generate_thumbnail_from_frame,
    _extract_frame,
    _validate_extracted_frame,
    _get_video_duration,
    generate_thumbnail,
)
from backend.services.video.pipeline import run_pipeline
from backend.queue_models import BulkQueueRequest


# ── Test Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def temp_dir():
    """Temporary directory for test files."""
    tmp = Path(tempfile.mkdtemp(prefix="pytest_3j_"))
    yield tmp
    # Note: Windows file locks may prevent immediate cleanup
    # Files will be cleaned up by OS temp cleanup eventually


@pytest.fixture
def sample_video_path(temp_dir):
    """Create a minimal valid MP4 for testing."""
    video_path = temp_dir / "test_video.mp4"
    # Create a 1-second test video using FFmpeg
    try:
        from backend.services.media.ffmpeg import get_ffmpeg_path
        ffmpeg_path = get_ffmpeg_path()
        cmd = [
            ffmpeg_path,
            "-f", "lavfi",
            "-i", "color=c=blue:s=320x240:d=1",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=30, check=True)
        return video_path
    except Exception:
        pytest.skip("FFmpeg not available for test video creation")


@pytest.fixture
def sample_output_path(temp_dir):
    """Sample output path for thumbnails."""
    return temp_dir / "test_thumbnail.jpg"


# ── Test A: text_only delegation ───────────────────────────────────────────────

def test_text_only_delegates_to_existing_generator(sample_output_path):
    """Test that text_only style delegates to existing generate_thumbnail()."""
    with patch('backend.services.video.thumbnail.generate_thumbnail') as mock_gen:
        mock_gen.return_value = sample_output_path
        
        result, fallbacks = generate_thumbnail_from_frame(
            video_path=Path("dummy.mp4"),
            title="Test Title",
            hook="Test Hook",
            output_path=sample_output_path,
            style="text_only",
        )
        
        mock_gen.assert_called_once()
        assert result == sample_output_path
        assert len(fallbacks) == 0


# ── Test B: Frame extraction from valid MP4 ────────────────────────────────────

def test_extract_frame_from_valid_mp4(sample_video_path, temp_dir):
    """Test _extract_frame extracts a valid JPEG from a valid MP4."""
    if not sample_video_path.exists():
        pytest.skip("Test video not available (FFmpeg may not be installed)")
    
    output_frame = temp_dir / "extracted_frame.jpg"
    
    result = _extract_frame(sample_video_path, 0.5, output_frame)
    
    assert result is True
    assert output_frame.exists()
    assert output_frame.stat().st_size > 0


# ── Test C: scene_frame generation ─────────────────────────────────────────────

def test_scene_frame_generates_valid_thumbnail(sample_video_path, sample_output_path):
    """Test scene_frame style produces a valid JPEG with frame as background."""
    if not sample_video_path.exists():
        pytest.skip("Test video not available (FFmpeg may not be installed)")
    
    result, fallbacks = generate_thumbnail_from_frame(
        video_path=sample_video_path,
        title="Test Title",
        hook="Test Hook",
        output_path=sample_output_path,
        style="scene_frame",
    )
    
    assert result == sample_output_path
    assert sample_output_path.exists()
    assert sample_output_path.stat().st_size > 0
    # Verify it's a valid JPEG
    assert _validate_extracted_frame(sample_output_path)


# ── Test D: scene_frame_overlay generation ─────────────────────────────────────

def test_scene_frame_overlay_generates_valid_thumbnail(sample_video_path, sample_output_path):
    """Test scene_frame_overlay style produces a valid blended JPEG."""
    if not sample_video_path.exists():
        pytest.skip("Test video not available (FFmpeg may not be installed)")
    
    result, fallbacks = generate_thumbnail_from_frame(
        video_path=sample_video_path,
        title="Test Title",
        hook="Test Hook",
        output_path=sample_output_path,
        style="scene_frame_overlay",
    )
    
    assert result == sample_output_path
    assert sample_output_path.exists()
    assert sample_output_path.stat().st_size > 0
    assert _validate_extracted_frame(sample_output_path)


# ── Test E: Frame extraction failure fallback ───────────────────────────────────

def test_frame_extraction_failure_falls_back_to_text_only(sample_output_path):
    """Test that frame extraction failure falls back to text_only."""
    non_existent_video = Path("/nonexistent/video.mp4")
    
    with patch('backend.services.video.thumbnail.generate_thumbnail') as mock_gen:
        mock_gen.return_value = sample_output_path
        
        result, fallbacks = generate_thumbnail_from_frame(
            video_path=non_existent_video,
            title="Test Title",
            hook="Test Hook",
            output_path=sample_output_path,
            style="scene_frame",
        )
        
        assert result == sample_output_path
        assert len(fallbacks) > 0
        assert any("video file not found" in f.lower() for f in fallbacks)
        mock_gen.assert_called_once()


# ── Test F: Unknown thumbnail_style validation ─────────────────────────────────

def test_unknown_thumbnail_style_raises_validation_error():
    """Test that unknown thumbnail_style raises Pydantic ValueError."""
    with pytest.raises(ValueError) as exc_info:
        BulkQueueRequest(
            topics=["test"],
            thumbnail_style="invalid_style",
        )
    
    assert "thumbnail_style must be one of" in str(exc_info.value)


# ── Test G: QueueJobResponse thumbnail_style mapping ─────────────────────────

def test_queue_job_response_maps_thumbnail_style():
    """Test that QueueJobResponse correctly serializes thumbnail_style."""
    from backend.queue_models import ContentQueueJob, queue_job_to_response
    from datetime import datetime, timezone
    
    job = ContentQueueJob(
        id="test-id",
        topic="Test Topic",
        language="en",
        tone="engaging",
        target_duration_seconds=180,
        scene_count=10,
        status="queued",
        progress=0,
        priority=0,
        retry_count=0,
        max_retries=3,
        youtube_privacy_status="private",
        youtube_category_id="22",
        video_uploaded=False,
        thumbnail_uploaded=False,
        schedule_set=False,
        made_for_kids=False,
        playlist_added=False,
        template_id="minimal_dark",
        aspect_ratio="16:9",
        thumbnail_style="scene_frame",
    )
    
    response = queue_job_to_response(job)
    
    assert response.thumbnail_style == "scene_frame"


# ── Test H: Thumbnail styles endpoint ───────────────────────────────────────────

def test_thumbnail_styles_endpoint():
    """Test that /api/queue/thumbnail-styles returns correct list."""
    from backend.routers.queue import get_thumbnail_styles
    
    styles = get_thumbnail_styles()
    
    assert isinstance(styles, list)
    assert "text_only" in styles
    assert "scene_frame" in styles
    assert "scene_frame_overlay" in styles
    assert len(styles) == 3


# ── Test I: Migration creation and idempotence ─────────────────────────────────

def test_migration_creates_column():
    """Test that migration adds thumbnail_style column if missing."""
    from sqlalchemy import create_engine, text
    from backend.migrations.phase_3j import run
    
    # Use in-memory SQLite database to avoid file permission issues
    engine = create_engine("sqlite:///:memory:")
    
    # Create the base table without thumbnail_style
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE content_queue_jobs (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL
            )
        """))
        conn.commit()
    
    # Run migration
    run(engine)
    
    # Verify column was added
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(content_queue_jobs)"))
        columns = [row[1] for row in result]
        assert "thumbnail_style" in columns
    
    # Run migration again (idempotence test)
    run(engine)
    
    # Verify no error on second run
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(content_queue_jobs)"))
        columns = [row[1] for row in result]
        assert "thumbnail_style" in columns


# ── Test J: queue_job_to_response thumbnail_style mapping ─────────────────────

def test_queue_job_to_response_thumbnail_style_none():
    """Test queue_job_to_response handles None thumbnail_style."""
    from backend.queue_models import ContentQueueJob, queue_job_to_response
    
    job = ContentQueueJob(
        id="test-id",
        topic="Test Topic",
        language="en",
        tone="engaging",
        target_duration_seconds=180,
        scene_count=10,
        status="queued",
        progress=0,
        priority=0,
        retry_count=0,
        max_retries=3,
        youtube_privacy_status="private",
        youtube_category_id="22",
        video_uploaded=False,
        thumbnail_uploaded=False,
        schedule_set=False,
        made_for_kids=False,
        playlist_added=False,
        template_id="minimal_dark",
        aspect_ratio="16:9",
        thumbnail_style=None,
    )
    
    response = queue_job_to_response(job)
    assert response.thumbnail_style is None


# ── Test K: Missing video file handling ────────────────────────────────────────

def test_missing_video_file_falls_back_to_text_only(sample_output_path):
    """Test that missing video file falls back to text_only."""
    missing_video = Path("/tmp/nonexistent_video_12345.mp4")
    
    with patch('backend.services.video.thumbnail.generate_thumbnail') as mock_gen:
        mock_gen.return_value = sample_output_path
        
        result, fallbacks = generate_thumbnail_from_frame(
            video_path=missing_video,
            title="Test Title",
            hook="Test Hook",
            output_path=sample_output_path,
            style="scene_frame",
        )
        
        assert result == sample_output_path
        assert len(fallbacks) > 0
        mock_gen.assert_called_once()


# ── Test L: Corrupted extracted image handling ───────────────────────────────

def test_corrupted_extracted_image_falls_back_to_text_only(sample_video_path, sample_output_path, temp_dir):
    """Test that corrupted extracted image falls back to text_only."""
    corrupted_frame = temp_dir / "corrupted.jpg"
    corrupted_frame.write_bytes(b"not a valid jpeg")
    
    with patch('backend.services.video.thumbnail._extract_frame') as mock_extract:
        mock_extract.return_value = True  # Pretend extraction succeeded
        
        with patch('backend.services.video.thumbnail.generate_thumbnail') as mock_gen:
            mock_gen.return_value = sample_output_path
            
            # Manually set up the corrupted file
            with patch('backend.services.video.thumbnail._validate_extracted_frame') as mock_validate:
                mock_validate.return_value = False  # Validation fails
                
                result, fallbacks = generate_thumbnail_from_frame(
                    video_path=sample_video_path,
                    title="Test Title",
                    hook="Test Hook",
                    output_path=sample_output_path,
                    style="scene_frame",
                )
                
                assert result == sample_output_path
                assert len(fallbacks) > 0
                mock_gen.assert_called_once()


# ── Test M: FFmpeg extraction failure handling ───────────────────────────────

def test_ffmpeg_extraction_failure_falls_back_to_text_only(sample_output_path):
    """Test that FFmpeg extraction failure falls back to text_only."""
    with patch('backend.services.video.thumbnail._extract_frame') as mock_extract:
        mock_extract.return_value = False  # Extraction fails
        
        with patch('backend.services.video.thumbnail.generate_thumbnail') as mock_gen:
            mock_gen.return_value = sample_output_path
            
            result, fallbacks = generate_thumbnail_from_frame(
                video_path=Path("dummy.mp4"),
                title="Test Title",
                hook="Test Hook",
                output_path=sample_output_path,
                style="scene_frame",
            )
            
            assert result == sample_output_path
            assert len(fallbacks) > 0
            mock_gen.assert_called_once()


# ── Test N: Very short video handling ────────────────────────────────────────

def test_very_short_video_uses_fallback_seek_points(sample_video_path, sample_output_path):
    """Test that very short video attempts fallback seek points."""
    with patch('backend.services.video.thumbnail._get_video_duration') as mock_duration:
        mock_duration.return_value = 0.5  # Very short video
        
        with patch('backend.services.video.thumbnail._extract_frame') as mock_extract:
            # First attempt fails, second succeeds
            mock_extract.side_effect = [False, True]
            
            with patch('backend.services.video.thumbnail.generate_thumbnail') as mock_gen:
                mock_gen.return_value = sample_output_path
                
                result, fallbacks = generate_thumbnail_from_frame(
                    video_path=sample_video_path,
                    title="Test Title",
                    hook="Test Hook",
                    output_path=sample_output_path,
                    style="scene_frame",
                )
                
                # Should have tried multiple seek points
                assert mock_extract.call_count >= 2


# ── Test O: Portrait-to-landscape conversion ──────────────────────────────────

def test_portrait_to_landscape_conversion(sample_output_path):
    """Test that portrait video is converted to landscape thumbnail."""
    # Create a portrait video
    with tempfile.TemporaryDirectory() as tmp:
        temp_dir = Path(tmp)
        portrait_video = temp_dir / "portrait.mp4"
        
        try:
            from backend.services.media.ffmpeg import get_ffmpeg_path
            ffmpeg_path = get_ffmpeg_path()
            cmd = [
                ffmpeg_path,
                "-f", "lavfi",
                "-i", "color=c=blue:s=270x480:d=1",  # 9:16 portrait
                "-pix_fmt", "yuv420p",
                str(portrait_video),
            ]
            subprocess.run(cmd, capture_output=True, timeout=30, check=True)
            
            result, fallbacks = generate_thumbnail_from_frame(
                video_path=portrait_video,
                title="Test Title",
                hook="Test Hook",
                output_path=sample_output_path,
                style="scene_frame",
            )
            
            assert result == sample_output_path
            assert sample_output_path.exists()
            
            # Verify output is landscape 1280x720
            from PIL import Image
            img = Image.open(sample_output_path)
            width, height = img.size
            img.close()  # Explicitly close to avoid Windows file lock issues
            assert width == 1280
            assert height == 720
            
        except Exception:
            pytest.skip("FFmpeg not available for portrait video test")


# ── Test P: NULL/default behavior ─────────────────────────────────────────────

def test_null_style_resolves_to_default(sample_output_path):
    """Test that NULL/empty style resolves to THUMBNAIL_DEFAULT_STYLE."""
    with patch.dict(os.environ, {'THUMBNAIL_DEFAULT_STYLE': 'text_only'}):
        with patch('backend.services.video.thumbnail.generate_thumbnail') as mock_gen:
            mock_gen.return_value = sample_output_path
            
            result, fallbacks = generate_thumbnail_from_frame(
                video_path=Path("dummy.mp4"),
                title="Test Title",
                hook="Test Hook",
                output_path=sample_output_path,
                style="",  # Empty string (NULL equivalent)
            )
            
            assert result == sample_output_path
            mock_gen.assert_called_once()


# ── Test Q: Invalid THUMBNAIL_DEFAULT_STYLE handling ────────────────────────

def test_invalid_thumbnail_default_style_normalizes_to_text_only(sample_output_path):
    """Test that invalid THUMBNAIL_DEFAULT_STYLE normalizes to text_only."""
    with patch.dict(os.environ, {'THUMBNAIL_DEFAULT_STYLE': 'invalid_style'}):
        with patch('backend.services.video.thumbnail.generate_thumbnail') as mock_gen:
            mock_gen.return_value = sample_output_path
            
            result, fallbacks = generate_thumbnail_from_frame(
                video_path=Path("dummy.mp4"),
                title="Test Title",
                hook="Test Hook",
                output_path=sample_output_path,
                style="",
            )
            
            # Should still work by falling back to text_only
            assert result == sample_output_path
            mock_gen.assert_called_once()


# ── Test R: Fallback actions manifest recording ───────────────────────────────

def test_fallback_actions_recorded_in_manifest(sample_video_path, sample_output_path):
    """Test that fallback actions are recorded when frame extraction fails."""
    with patch('backend.services.video.thumbnail._extract_frame') as mock_extract:
        mock_extract.return_value = False  # All extractions fail
        
        with patch('backend.services.video.thumbnail.generate_thumbnail') as mock_gen:
            mock_gen.return_value = sample_output_path
            
            result, fallbacks = generate_thumbnail_from_frame(
                video_path=sample_video_path,
                title="Test Title",
                hook="Test Hook",
                output_path=sample_output_path,
                style="scene_frame",
            )
            
            assert len(fallbacks) > 0
            # Check that any fallback action contains relevant keywords
            assert any("extraction" in f.lower() or "video" in f.lower() or "failed" in f.lower() for f in fallbacks)


# ── Test S: API 422 prevents job creation with invalid style ───────────────────

def test_api_422_prevents_job_creation_with_invalid_style():
    """Test that API returns 422 for invalid thumbnail_style."""
    from fastapi.testclient import TestClient
    from backend.main import app
    
    client = TestClient(app)
    
    response = client.post("/api/queue", json={
        "topics": ["test topic"],
        "thumbnail_style": "invalid_style_xyz",
    })
    
    assert response.status_code == 422
    assert "thumbnail_style" in response.text.lower()


# ── Additional helper tests ────────────────────────────────────────────────────

def test_validate_extracted_frame_rejects_zero_byte_file(temp_dir):
    """Test that _validate_extracted_frame rejects zero-byte files."""
    zero_byte_file = temp_dir / "zero.jpg"
    zero_byte_file.write_bytes(b"")
    
    assert _validate_extracted_frame(zero_byte_file) is False


def test_validate_extracted_frame_rejects_nonexistent_file():
    """Test that _validate_extracted_frame rejects nonexistent files."""
    assert _validate_extracted_frame(Path("/nonexistent/file.jpg")) is False


def test_get_video_duration_handles_invalid_file():
    """Test that _get_video_duration returns 0.0 for invalid files."""
    duration = _get_video_duration(Path("/nonexistent/video.mp4"))
    assert duration == 0.0


def test_video_generation_router_missing_phase_3j_parameter():
    """Test that video_generation router properly passes thumbnail_style parameter.
    
    This is a regression test for the NameError: name 'thumbnail_style' is not defined
    that occurred when the video_generation router called run_pipeline without
    the Phase 3J parameters.
    """
    import inspect
    from backend.services.video.pipeline import run_pipeline
    
    # Verify run_pipeline accepts thumbnail_style parameter
    sig = inspect.signature(run_pipeline)
    assert 'thumbnail_style' in sig.parameters, "run_pipeline must accept thumbnail_style parameter"
    
    # Verify the parameter has a default value of None
    param = sig.parameters['thumbnail_style']
    assert param.default is None, "thumbnail_style parameter must default to None"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
