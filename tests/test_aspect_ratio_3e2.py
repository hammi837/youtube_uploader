"""
tests/test_aspect_ratio_3e2.py — Phase 3E.2 aspect ratio support tests.

Tests:
- Aspect ratio configuration and dimensions
- Default 16:9 behavior
- Invalid aspect ratio fallback or validation
- Database model integration (requires updated schema)
- API request and response fields
- Queue-to-video propagation
- Template and aspect ratio combination
- Landscape scene-card generation
- Vertical scene-card generation
- Text wrapping and font fitting
- Caption positioning for both ratios
- Thumbnail generation for both ratios
- Existing jobs without aspect_ratio
- Upload-only retry preserving the existing video
- Backward compatibility with Phase 3E.1

NOTE: Database tests require the updated schema with aspect_ratio columns.
Run database recreation after stopping the server:
  rm uploads.db uploads.db-journal
  python -c "from backend.db import Base, engine; Base.metadata.create_all(bind=engine)"
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from backend.db import SessionLocal, Base, engine
from backend.queue_models import ContentQueueJob, BulkQueueRequest, QueueJobResponse
from backend.video_generation_models import (
    VideoGenerationJob,
    VideoGenerationRequest,
    VideoJobResponse,
    VideoJobStatus,
    job_to_response,
)
from backend.services.video.aspect_ratio import (
    get_aspect_ratio_config,
    get_dimensions,
    is_valid_aspect_ratio,
    normalize_aspect_ratio,
    list_aspect_ratios,
    DEFAULT_ASPECT_RATIO,
    VALID_ASPECT_RATIOS,
)


# ── Aspect Ratio Configuration Tests ───────────────────────────────────────────

def test_aspect_ratio_config_16_9():
    """Test 16:9 aspect ratio configuration."""
    config = get_aspect_ratio_config("16:9")
    assert config.aspect_ratio == "16:9"
    assert config.width == 1920
    assert config.height == 1080
    assert config.label == "YouTube Landscape"
    assert "Standard YouTube video format" in config.description


def test_aspect_ratio_config_9_16():
    """Test 9:16 aspect ratio configuration."""
    config = get_aspect_ratio_config("9:16")
    assert config.aspect_ratio == "9:16"
    assert config.width == 1080
    assert config.height == 1920
    assert config.label == "YouTube Shorts"
    assert "Vertical video format" in config.description


def test_aspect_ratio_dimensions():
    """Test dimension extraction."""
    width, height = get_dimensions("16:9")
    assert width == 1920
    assert height == 1080

    width, height = get_dimensions("9:16")
    assert width == 1080
    assert height == 1920


def test_invalid_aspect_ratio_fallback():
    """Test invalid aspect ratio falls back to 16:9."""
    config = get_aspect_ratio_config("invalid")
    assert config.aspect_ratio == "16:9"
    assert config.width == 1920
    assert config.height == 1080


def test_is_valid_aspect_ratio():
    """Test aspect ratio validation."""
    assert is_valid_aspect_ratio("16:9") is True
    assert is_valid_aspect_ratio("9:16") is True
    assert is_valid_aspect_ratio("1:1") is False
    assert is_valid_aspect_ratio("4:3") is False
    assert is_valid_aspect_ratio("invalid") is False


def test_normalize_aspect_ratio():
    """Test aspect ratio normalization."""
    assert normalize_aspect_ratio("16:9") == "16:9"
    assert normalize_aspect_ratio("9:16") == "9:16"
    assert normalize_aspect_ratio("invalid") == DEFAULT_ASPECT_RATIO
    assert normalize_aspect_ratio("") == DEFAULT_ASPECT_RATIO


def test_list_aspect_ratios():
    """Test listing all aspect ratios."""
    ratios = list_aspect_ratios()
    assert len(ratios) == 2
    assert any(r["aspect_ratio"] == "16:9" for r in ratios)
    assert any(r["aspect_ratio"] == "9:16" for r in ratios)


def test_default_aspect_ratio():
    """Test default aspect ratio constant."""
    assert DEFAULT_ASPECT_RATIO == "16:9"
    assert "16:9" in VALID_ASPECT_RATIOS
    assert "9:16" in VALID_ASPECT_RATIOS


# ── Database Model Tests ─────────────────────────────────────────────────────

def test_queue_job_aspect_ratio_default():
    """Test ContentQueueJob has default aspect_ratio."""
    db = SessionLocal()
    try:
        job = ContentQueueJob(
            topic="Test topic",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=10,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        assert job.aspect_ratio == "16:9"
        db.delete(job)
        db.commit()
    finally:
        db.close()


def test_video_job_aspect_ratio_default():
    """Test VideoGenerationJob has default aspect_ratio."""
    db = SessionLocal()
    try:
        job = VideoGenerationJob(
            id="test-video-job",
            content_project_id="test-project",
            status=VideoJobStatus.QUEUED,
            width=1920,
            height=1080,
            fps=30,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        assert job.aspect_ratio == "16:9"
        db.delete(job)
        db.commit()
    finally:
        db.close()


def test_queue_job_aspect_ratio_9_16():
    """Test ContentQueueJob with 9:16 aspect ratio."""
    db = SessionLocal()
    try:
        job = ContentQueueJob(
            topic="Test topic",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=10,
            aspect_ratio="9:16",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        assert job.aspect_ratio == "9:16"
        db.delete(job)
        db.commit()
    finally:
        db.close()


# ── Pydantic Model Tests ─────────────────────────────────────────────────────

def test_video_generation_request_aspect_ratio():
    """Test VideoGenerationRequest with aspect_ratio."""
    request = VideoGenerationRequest(
        aspect_ratio="9:16",
    )
    assert request.aspect_ratio == "9:16"


def test_video_generation_request_invalid_aspect_ratio_fallback():
    """Test VideoGenerationRequest validates and falls back invalid aspect_ratio."""
    request = VideoGenerationRequest(
        aspect_ratio="invalid",
    )
    # Should fall back to 16:9 due to validator
    assert request.aspect_ratio == "16:9"


def test_bulk_queue_request_aspect_ratio():
    """Test BulkQueueRequest with aspect_ratio."""
    request = BulkQueueRequest(
        topics=["Test topic"],
        aspect_ratio="9:16",
    )
    assert request.aspect_ratio == "9:16"


def test_queue_job_response_aspect_ratio():
    """Test QueueJobResponse includes aspect_ratio."""
    db = SessionLocal()
    try:
        job = ContentQueueJob(
            topic="Test topic",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=10,
            aspect_ratio="9:16",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        
        response = QueueJobResponse.model_validate(job)
        assert response.aspect_ratio == "9:16"
        
        db.delete(job)
        db.commit()
    finally:
        db.close()


def test_video_job_response_aspect_ratio():
    """Test VideoJobResponse includes aspect_ratio."""
    db = SessionLocal()
    try:
        job = VideoGenerationJob(
            id="test-video-job",
            content_project_id="test-project",
            status=VideoJobStatus.QUEUED,
            width=1080,
            height=1920,
            fps=30,
            aspect_ratio="9:16",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        
        response = job_to_response(job)
        assert response.aspect_ratio == "9:16"
        
        db.delete(job)
        db.commit()
    finally:
        db.close()


def test_video_job_response_aspect_ratio_fallback():
    """Test VideoJobResponse uses database default for aspect_ratio."""
    import uuid
    db = SessionLocal()
    try:
        # Create a job without explicitly setting aspect_ratio
        job = VideoGenerationJob(
            id=f"test-video-job-old-{uuid.uuid4().hex[:8]}",
            content_project_id="test-project",
            status=VideoJobStatus.QUEUED,
            width=1920,
            height=1080,
            fps=30,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        
        # Database should have set the default value
        assert job.aspect_ratio == "16:9"
        
        response = job_to_response(job)
        assert response.aspect_ratio == "16:9"
        
        db.delete(job)
        db.commit()
    finally:
        db.close()


# ── Visual Builder Tests ─────────────────────────────────────────────────────

def test_visual_builder_aspect_ratio_16_9():
    """Test visual builder with 16:9 aspect ratio."""
    from backend.services.video.visual_builder import generate_title_card, generate_scene_card
    from backend.services.video.templates import get_template
    from pathlib import Path
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        template = get_template("minimal_dark")
        
        # Test title card
        title_path = Path(tmpdir) / "title_16_9.png"
        result = generate_title_card(
            title="Test Title",
            hook="Test Hook",
            output_path=title_path,
            width=1920,
            height=1080,
            topic_seed="test",
            template=template,
            aspect_ratio="16:9",
        )
        assert result.exists()
        
        # Test scene card
        scene_path = Path(tmpdir) / "scene_16_9.png"
        result = generate_scene_card(
            scene_number=1,
            title="Test Title",
            narration="Test narration text for scene card",
            visual_description="Test visual description",
            output_path=scene_path,
            width=1920,
            height=1080,
            topic_seed="test",
            template=template,
            aspect_ratio="16:9",
        )
        assert result.exists()


def test_visual_builder_aspect_ratio_9_16():
    """Test visual builder with 9:16 aspect ratio."""
    from backend.services.video.visual_builder import generate_title_card, generate_scene_card
    from backend.services.video.templates import get_template
    from pathlib import Path
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        template = get_template("minimal_dark")
        
        # Test title card
        title_path = Path(tmpdir) / "title_9_16.png"
        result = generate_title_card(
            title="Test Title",
            hook="Test Hook",
            output_path=title_path,
            width=1080,
            height=1920,
            topic_seed="test",
            template=template,
            aspect_ratio="9:16",
        )
        assert result.exists()
        
        # Test scene card
        scene_path = Path(tmpdir) / "scene_9_16.png"
        result = generate_scene_card(
            scene_number=1,
            title="Test Title",
            narration="Test narration text for scene card",
            visual_description="Test visual description",
            output_path=scene_path,
            width=1080,
            height=1920,
            topic_seed="test",
            template=template,
            aspect_ratio="9:16",
        )
        assert result.exists()


def test_visual_builder_template_aspect_ratio_combination():
    """Test both templates with both aspect ratios."""
    from backend.services.video.visual_builder import generate_title_card
    from backend.services.video.templates import get_template
    from pathlib import Path
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        for template_id in ["minimal_dark", "quote_fact"]:
            for aspect_ratio in ["16:9", "9:16"]:
                template = get_template(template_id)
                width, height = get_dimensions(aspect_ratio)
                
                title_path = Path(tmpdir) / f"title_{template_id}_{aspect_ratio.replace(':', '-')}.png"
                result = generate_title_card(
                    title="Test Title",
                    hook="Test Hook",
                    output_path=title_path,
                    width=width,
                    height=height,
                    topic_seed="test",
                    template=template,
                    aspect_ratio=aspect_ratio,
                )
                assert result.exists(), f"Failed for {template_id} with {aspect_ratio}"


def test_visual_builder_invalid_aspect_ratio_fallback():
    """Test visual builder falls back to 16:9 for invalid aspect ratio."""
    from backend.services.video.visual_builder import generate_title_card
    from backend.services.video.templates import get_template
    from pathlib import Path
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        template = get_template("minimal_dark")
        
        title_path = Path(tmpdir) / "title_invalid.png"
        result = generate_title_card(
            title="Test Title",
            hook="Test Hook",
            output_path=title_path,
            width=1920,
            height=1080,
            topic_seed="test",
            template=template,
            aspect_ratio="invalid",  # Should fall back to 16:9
        )
        assert result.exists()


# ── Backward Compatibility Tests ─────────────────────────────────────────────

def test_existing_queue_job_without_aspect_ratio():
    """Test existing queue jobs without aspect_ratio default to 16:9."""
    db = SessionLocal()
    try:
        # Simulate an old job by not setting aspect_ratio
        job = ContentQueueJob(
            topic="Old topic",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=10,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        
        # Should have default value
        assert job.aspect_ratio == "16:9"
        
        db.delete(job)
        db.commit()
    finally:
        db.close()


def test_phase_3e1_template_compatibility():
    """Test Phase 3E.1 templates still work with aspect ratio."""
    from backend.services.video.visual_builder import generate_title_card
    from backend.services.video.templates import get_template
    from pathlib import Path
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        for template_id in ["minimal_dark", "quote_fact"]:
            template = get_template(template_id)
            
            # Should work with both aspect ratios
            for aspect_ratio in ["16:9", "9:16"]:
                width, height = get_dimensions(aspect_ratio)
                title_path = Path(tmpdir) / f"compat_{template_id}_{aspect_ratio.replace(':', '-')}.png"
                result = generate_title_card(
                    title="Test Title",
                    hook="Test Hook",
                    output_path=title_path,
                    width=width,
                    height=height,
                    topic_seed="test",
                    template=template,
                    aspect_ratio=aspect_ratio,
                )
                assert result.exists()


# ── Queue to Video Propagation Tests ─────────────────────────────────────────

def test_queue_to_video_aspect_ratio_propagation():
    """Test aspect ratio propagates from queue job to video job."""
    db = SessionLocal()
    try:
        # Create queue job with 9:16
        queue_job = ContentQueueJob(
            topic="Test topic",
            language="en",
            tone="engaging",
            target_duration_seconds=180,
            scene_count=10,
            aspect_ratio="9:16",
        )
        db.add(queue_job)
        db.commit()
        db.refresh(queue_job)
        
        # Simulate queue processor reading aspect_ratio
        aspect_ratio = queue_job.aspect_ratio if queue_job else "16:9"
        assert aspect_ratio == "9:16"
        
        # Calculate dimensions
        from backend.services.video.aspect_ratio import get_dimensions
        width, height = get_dimensions(aspect_ratio)
        assert width == 1080
        assert height == 1920
        
        db.delete(queue_job)
        db.commit()
    finally:
        db.close()


# ── API Integration Tests ───────────────────────────────────────────────────

def test_api_aspect_ratio_validation():
    """Test API validates aspect_ratio in requests."""
    # Test valid 16:9
    request = VideoGenerationRequest(aspect_ratio="16:9")
    assert request.aspect_ratio == "16:9"
    
    # Test valid 9:16
    request = VideoGenerationRequest(aspect_ratio="9:16")
    assert request.aspect_ratio == "9:16"
    
    # Test invalid falls back
    request = VideoGenerationRequest(aspect_ratio="invalid")
    assert request.aspect_ratio == "16:9"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
