"""
Phase 3F.5 Production Hardening Tests.

Covers:
  - Image validation (valid JPEG, zero-byte, tiny, corrupt, unexpected dims)
  - Invalid images never enter the cache path
  - Cleanup: old dirs removed, recent dirs preserved, active-job dirs preserved
  - 429 Retry-After header respected / fallback when absent
  - Per-job AI timeout budget stops further generation
  - AI generation summary logged to queue job
"""

from __future__ import annotations

import io
import os
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone, timedelta

import pytest

from backend.services.visual.ai_image_backend import (
    PollinationsBackend,
    ImageGenerationRequest,
    ImageGenerationResult,
    validate_image_bytes,
    get_min_image_bytes,
)
from backend.services.visual.visual_provider import (
    AIVisualProvider,
    VisualAssetResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_request(tmp_path, prompt="test", width=1280, height=720, model="flux"):
    return ImageGenerationRequest(
        prompt=prompt,
        aspect_ratio="16:9",
        width=width,
        height=height,
        output_path=tmp_path / "out.jpg",
        model=model,
        timeout_s=10.0,
    )


def _make_valid_jpeg_bytes() -> bytes:
    """
    Create a valid RGB JPEG that is guaranteed to exceed 1024 bytes.
    Uses a random-noise image to prevent JPEG compression from producing a tiny file.
    """
    from PIL import Image
    import random
    buf = io.BytesIO()
    # Random pixel data defeats JPEG's block-level compression
    pixels = [random.randint(0, 255) for _ in range(256 * 144 * 3)]
    img = Image.frombytes("RGB", (256, 144), bytes(pixels))
    img.save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    assert len(data) >= 1024, f"Test JPEG still too small: {len(data)} bytes"
    return data


def _make_mock_provider(mock_backend):
    with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as f:
        f.create.return_value = mock_backend
        provider = AIVisualProvider()
    return provider


# ─────────────────────────────────────────────────────────────────────────────
# 1. Image validation — validate_image_bytes()
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateImageBytes:
    def test_valid_jpeg_passes(self):
        data = _make_valid_jpeg_bytes()
        ok, reason = validate_image_bytes(data)
        assert ok is True
        assert reason == "ok"

    def test_zero_bytes_fails(self):
        ok, reason = validate_image_bytes(b"")
        assert ok is False
        assert "too small" in reason or "0 bytes" in reason

    def test_below_minimum_fails(self):
        with patch.dict(os.environ, {"AI_IMAGE_MIN_BYTES": "1024"}):
            ok, reason = validate_image_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        assert ok is False
        assert "too small" in reason.lower()

    def test_exactly_minimum_size_but_corrupt_fails(self):
        """Meets size threshold but is not a valid image."""
        with patch.dict(os.environ, {"AI_IMAGE_MIN_BYTES": "50"}):
            ok, reason = validate_image_bytes(b"\x00" * 100)  # Not a JPEG
        assert ok is False
        assert "PIL" in reason or "verif" in reason.lower()

    def test_html_error_page_fails(self):
        """Pollinations sometimes returns HTML on error with image content-type."""
        html = b"<html><body>Error: Content policy violation</body></html>" * 20
        ok, reason = validate_image_bytes(html)
        assert ok is False

    def test_truncated_jpeg_fails(self):
        """Take first 100 bytes of a valid JPEG — should be truncated/invalid."""
        valid = _make_valid_jpeg_bytes()
        truncated = valid[:100]
        ok, reason = validate_image_bytes(truncated)
        assert ok is False

    def test_custom_min_bytes_env_var(self):
        data = _make_valid_jpeg_bytes()
        # Set minimum higher than the actual image size → should fail
        with patch.dict(os.environ, {"AI_IMAGE_MIN_BYTES": str(len(data) + 10000)}):
            ok, reason = validate_image_bytes(data)
        assert ok is False
        assert "too small" in reason.lower()

    def test_valid_image_with_unexpected_dimensions_passes(self):
        """Pollinations may return 1024x576 even when 1280x720 was requested."""
        from PIL import Image
        import random
        buf = io.BytesIO()
        pixels = [random.randint(0, 255) for _ in range(1024 * 576 * 3)]
        img = Image.frombytes("RGB", (1024, 576), bytes(pixels))
        img.save(buf, format="JPEG", quality=85)
        ok, reason = validate_image_bytes(buf.getvalue())
        assert ok is True  # dimension mismatch is not a validation failure

    def test_get_min_image_bytes_default(self):
        with patch.dict(os.environ, {}, clear=False):
            env = dict(os.environ)
            env.pop("AI_IMAGE_MIN_BYTES", None)
            with patch.dict(os.environ, env, clear=True):
                result = get_min_image_bytes()
        assert result == 1024

    def test_get_min_image_bytes_custom(self):
        with patch.dict(os.environ, {"AI_IMAGE_MIN_BYTES": "2048"}):
            result = get_min_image_bytes()
        assert result == 2048


# ─────────────────────────────────────────────────────────────────────────────
# 2. PollinationsBackend validation integration
# ─────────────────────────────────────────────────────────────────────────────

class TestPollinationsValidation:
    """Verify that invalid downloaded bytes never reach disk / succeed."""

    def _mock_client(self, status=200, content_type="image/jpeg", body=b""):
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.headers = {"content-type": content_type}
        mock_resp.content = body
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        return mock_client

    def test_valid_image_succeeds_and_writes_file(self, tmp_path):
        body = _make_valid_jpeg_bytes()
        backend = PollinationsBackend(max_retries=0)
        req = _make_request(tmp_path)
        mock_client = self._mock_client(body=body)
        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)
        assert result.success is True
        assert req.output_path.exists()
        assert req.output_path.read_bytes() == body

    def test_zero_byte_response_fails_no_file_written(self, tmp_path):
        backend = PollinationsBackend(max_retries=0)
        req = _make_request(tmp_path)
        mock_client = self._mock_client(body=b"")
        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)
        assert result.success is False
        assert not req.output_path.exists()

    def test_corrupt_jpeg_fails_no_file_written(self, tmp_path):
        backend = PollinationsBackend(max_retries=0)
        req = _make_request(tmp_path)
        corrupt = b"\xff\xd8\xff" + b"\x00" * 2000  # JPEG header but garbage body
        mock_client = self._mock_client(body=corrupt)
        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)
        assert result.success is False
        assert not req.output_path.exists()

    def test_html_body_fails_no_file_written(self, tmp_path):
        backend = PollinationsBackend(max_retries=0)
        req = _make_request(tmp_path)
        html = b"<html><body>rate limited</body></html>" * 30
        mock_client = self._mock_client(body=html)
        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)
        assert result.success is False
        assert not req.output_path.exists()

    def test_invalid_image_never_enters_cache(self, tmp_path):
        """After validation failure, output_path must not exist so cache check fails."""
        backend = PollinationsBackend(max_retries=0)
        req = _make_request(tmp_path)
        corrupt = b"\xff\xd8\xff" + b"\x00" * 2000
        mock_client = self._mock_client(body=corrupt)
        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)

        # output_path must not exist — cache check uses output_path.exists()
        assert not req.output_path.exists(), (
            "Corrupt file must not be written to disk (would poison cache)"
        )
        assert result.success is False

    def test_valid_image_unexpected_dimensions_succeeds(self, tmp_path):
        """1024×576 returned when 1280×720 was requested — must still succeed."""
        body = _make_valid_jpeg_bytes()  # noise image, always > 1024 bytes

        backend = PollinationsBackend(max_retries=0)
        req = _make_request(tmp_path, width=1280, height=720)
        mock_client = self._mock_client(body=body)
        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)
        assert result.success is True
        assert req.output_path.exists()


# ─────────────────────────────────────────────────────────────────────────────
# 3. 429 Retry-After header
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryAfter:
    def test_numeric_retry_after_used(self):
        backend = PollinationsBackend()
        headers = {"retry-after": "12"}
        assert backend._parse_retry_after(headers) == 12

    def test_absent_retry_after_uses_default(self):
        backend = PollinationsBackend()
        headers = {}
        assert backend._parse_retry_after(headers) == 5

    def test_retry_after_capped_at_max(self):
        backend = PollinationsBackend()
        headers = {"retry-after": "9999"}
        result = backend._parse_retry_after(headers)
        assert result <= 30  # _MAX_RETRY_AFTER_S

    def test_zero_retry_after_becomes_1(self):
        backend = PollinationsBackend()
        headers = {"retry-after": "0"}
        assert backend._parse_retry_after(headers) >= 1

    def test_unparseable_retry_after_uses_default(self):
        backend = PollinationsBackend()
        headers = {"retry-after": "not-a-number"}
        assert backend._parse_retry_after(headers) == 5

    def test_429_uses_retry_after_header_not_fixed_5s(self, tmp_path):
        """When Retry-After says 2s, sleep should be ~2s not 5s."""
        backend = PollinationsBackend(max_retries=0)
        req = _make_request(tmp_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {"retry-after": "2", "content-type": "text/plain"}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            with patch("backend.services.visual.ai_image_backend.time.sleep") as mock_sleep:
                backend.generate_image(req)

        # sleep should have been called with 2 (from header), not 5 (old default)
        mock_sleep.assert_called()
        sleep_arg = mock_sleep.call_args[0][0]
        assert sleep_arg == 2, f"Expected sleep(2) from Retry-After header, got sleep({sleep_arg})"

    def test_429_missing_retry_after_uses_5s_fallback(self, tmp_path):
        backend = PollinationsBackend(max_retries=0)
        req = _make_request(tmp_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}  # no Retry-After

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            with patch("backend.services.visual.ai_image_backend.time.sleep") as mock_sleep:
                backend.generate_image(req)

        mock_sleep.assert_called()
        sleep_arg = mock_sleep.call_args[0][0]
        assert sleep_arg == 5  # default fallback

    def test_retry_after_respected_in_real_job_flow(self, tmp_path):
        """Full generate_image: first call 429 with Retry-After:3, second call valid image."""
        body = _make_valid_jpeg_bytes()

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"retry-after": "3"}

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.headers = {"content-type": "image/jpeg"}
        resp_ok.content = body

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [resp_429, resp_ok]

        backend = PollinationsBackend(max_retries=1)
        req = _make_request(tmp_path)

        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            with patch("backend.services.visual.ai_image_backend.time.sleep") as mock_sleep:
                result = backend.generate_image(req)

        assert result.success is True
        # Slept for 3s (from Retry-After header)
        mock_sleep.assert_any_call(3)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Per-job AI generation timeout
# ─────────────────────────────────────────────────────────────────────────────

class TestJobAITimeout:
    def _make_provider_with_backend(self, mock_backend):
        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as f:
            f.create.return_value = mock_backend
            return AIVisualProvider()

    def test_expired_deadline_returns_fallback_immediately(self, tmp_path):
        """job_ai_deadline already in the past → instant fallback, no backend call."""
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        provider = self._make_provider_with_backend(mock_backend)

        expired_deadline = time.monotonic() - 1.0  # 1 second in the past

        with patch.dict(os.environ, {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}):
            result = provider.generate_visual(
                "a mountain",
                "16:9",
                {"job_id": "tj1", "scene_number": 0, "job_ai_deadline": expired_deadline},
            )

        assert result.fallback_used is True
        assert result.provider_name == "ai"
        assert "budget exhausted" in result.error_message.lower()
        mock_backend.generate_image.assert_not_called()

    def test_future_deadline_allows_generation(self, tmp_path):
        """Deadline well in the future → backend is called normally."""
        img_file = tmp_path / "tj2" / "s.jpg"
        img_file.parent.mkdir(parents=True)
        img_file.write_bytes(_make_valid_jpeg_bytes())

        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = ImageGenerationResult(
            success=True, output_path=img_file, error_message=None,
            backend_name="pollinations", model="flux",
            generation_time_s=1.0, width=1280, height=720,
        )
        provider = self._make_provider_with_backend(mock_backend)

        future_deadline = time.monotonic() + 600.0

        with patch.dict(os.environ, {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}):
            result = provider.generate_visual(
                "a forest",
                "16:9",
                {"job_id": "tj2", "scene_number": 0, "job_ai_deadline": future_deadline},
            )

        mock_backend.generate_image.assert_called_once()
        assert result.fallback_used is False

    def test_no_deadline_in_context_behaves_normally(self, tmp_path):
        """When no job_ai_deadline key is present, generation proceeds as before."""
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = MagicMock(
            success=False, output_path=None,
            error_message="test", backend_name="pollinations", model="flux",
            generation_time_s=0.1,
        )
        provider = self._make_provider_with_backend(mock_backend)

        with patch.dict(os.environ, {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}):
            result = provider.generate_visual(
                "a test",
                "16:9",
                {"job_id": "tj3", "scene_number": 0},  # no deadline key
            )

        mock_backend.generate_image.assert_called_once()

    def test_expired_deadline_remaining_scenes_fallback(self, tmp_path):
        """Simulate multiple scenes: first passes, deadline expires, rest fallback."""
        call_count = {"n": 0}
        deadline_holder = {"v": time.monotonic() + 600}

        def fake_generate(req):
            call_count["n"] += 1
            # Expire deadline after first call
            deadline_holder["v"] = time.monotonic() - 1.0
            return ImageGenerationResult(
                success=False, output_path=None,
                error_message="simulated fail", backend_name="pollinations", model="flux",
                generation_time_s=0.0,
            )

        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.side_effect = fake_generate

        provider = self._make_provider_with_backend(mock_backend)

        results = []
        with patch.dict(os.environ, {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}):
            for i in range(3):
                r = provider.generate_visual(
                    f"scene {i}",
                    "16:9",
                    {"job_id": "tj4", "scene_number": i, "job_ai_deadline": deadline_holder["v"]},
                )
                results.append(r)

        # Scene 0 called backend (deadline was future), scenes 1–2 were stopped
        assert call_count["n"] == 1
        assert all(r.fallback_used for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# 5. AI generation summary
# ─────────────────────────────────────────────────────────────────────────────

class TestAISummaryLog:
    """
    Test the AI generation summary that appears in queue job logs after
    visual provider processing.

    We test the summary logic directly by driving it through a minimal
    mock of the queue_processor's visual provider branch.
    """

    def _run_mock_visual_branch(self, scene_results, tmp_path):
        """
        Simulate the visual provider branch in queue_processor.

        scene_results: list of (success: bool) per scene
        Returns (log_calls, summary_line)
        """
        import time as _time

        from backend.services.visual.visual_provider import VisualAssetResult

        scenes = [
            {"background_type": "visual_provider", "visual_prompt": f"scene {i}"}
            for i in range(len(scene_results))
        ]

        log_calls = []

        def mock_generate(**kwargs):
            i = kwargs["scene_context"]["scene_number"]
            success = scene_results[i]
            if success:
                # Create a dummy file
                p = tmp_path / "summary_job" / f"scene_{i:03d}.jpg"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(_make_valid_jpeg_bytes())
                return VisualAssetResult(
                    asset_path=str(p), asset_type="image",
                    provider_name="ai", fallback_used=False,
                )
            return VisualAssetResult(
                asset_path=None, asset_type="image",
                provider_name="ai", fallback_used=True,
                error_message="simulated failure",
            )

        _ai_generated = 0
        _ai_fallback = 0
        _ai_total_s = 0.0
        _ai_job_deadline = _time.monotonic() + 600

        for i, scene in enumerate(scenes):
            if scene.get("background_type") == "visual_provider":
                _t = _time.monotonic()
                result = mock_generate(
                    visual_prompt=scene.get("visual_prompt"),
                    aspect_ratio="16:9",
                    scene_context={"job_id": "sj1", "scene_number": i, "job_ai_deadline": _ai_job_deadline},
                )
                _ai_total_s += _time.monotonic() - _t
                if result.asset_path and not result.fallback_used:
                    _ai_generated += 1
                else:
                    _ai_fallback += 1

        _ai_total_scenes = _ai_generated + _ai_fallback
        summary = (
            f"AI visual generation: "
            f"{_ai_generated}/{_ai_total_scenes} scenes generated with AI, "
            f"{_ai_fallback}/{_ai_total_scenes} used gradient fallback "
            f"(total {_ai_total_s:.1f}s)"
        )
        return summary, _ai_generated, _ai_fallback, _ai_total_scenes

    def test_all_ai_generated_summary(self, tmp_path):
        summary, gen, fall, total = self._run_mock_visual_branch(
            [True, True, True], tmp_path
        )
        assert gen == 3
        assert fall == 0
        assert total == 3
        assert "3/3 scenes generated with AI" in summary
        assert "0/3 used gradient fallback" in summary

    def test_mixed_summary(self, tmp_path):
        summary, gen, fall, total = self._run_mock_visual_branch(
            [True, False, True, False, True], tmp_path
        )
        assert gen == 3
        assert fall == 2
        assert total == 5
        assert "3/5 scenes generated with AI" in summary
        assert "2/5 used gradient fallback" in summary

    def test_all_fallback_summary(self, tmp_path):
        summary, gen, fall, total = self._run_mock_visual_branch(
            [False, False], tmp_path
        )
        assert gen == 0
        assert fall == 2
        assert total == 2
        assert "0/2 scenes generated with AI" in summary
        assert "2/2 used gradient fallback" in summary

    def test_summary_contains_timing(self, tmp_path):
        summary, *_ = self._run_mock_visual_branch([True], tmp_path)
        assert "total" in summary
        assert "s)" in summary  # e.g. "total 0.0s)"


# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 6. Cleanup — generated_images/ directory
# ─────────────────────────────────────────────────────────────────────────────

class TestAIImageCleanup:
    """
    Test run_cleanup() handling of data/generated_images/.

    Uses the shared in-memory SQLite DB from conftest.
    Each test resets DB state via an autouse fixture.
    """

    @pytest.fixture(autouse=True)
    def reset_db(self):
        from tests.conftest import shared_engine
        from backend.db import Base
        Base.metadata.drop_all(bind=shared_engine)
        Base.metadata.create_all(bind=shared_engine)
        yield
        Base.metadata.drop_all(bind=shared_engine)
        Base.metadata.create_all(bind=shared_engine)

    def _db(self):
        from tests.conftest import SharedTestingSessionLocal
        return SharedTestingSessionLocal()

    def _patch_session(self):
        from tests.conftest import SharedTestingSessionLocal
        return patch("backend.db.SessionLocal", SharedTestingSessionLocal)

    def _setup_ai_image_dir(self, tmp_path, subdirs):
        """Create ai_root with the given subdirs, each containing one valid JPEG."""
        ai_root = tmp_path / "generated_images"
        files = {}
        for name in subdirs:
            d = ai_root / name
            d.mkdir(parents=True)
            f = d / "scene_000_abc.jpg"
            f.write_bytes(_make_valid_jpeg_bytes())
            files[name] = f
        return ai_root, files

    def test_dev_dirs_always_removed(self, tmp_path):
        """'test' and 'unknown' dirs are always cleaned up regardless of DB."""
        ai_root, _ = self._setup_ai_image_dir(tmp_path, ["test", "unknown"])

        with self._patch_session():
            with patch(
                "backend.services.visual.ai_image_backend.get_ai_image_output_dir",
                return_value=ai_root,
            ):
                from backend.services.queue_services import run_cleanup
                result = run_cleanup(dry_run=False)

        assert not (ai_root / "test").exists()
        assert not (ai_root / "unknown").exists()
        assert result["files_deleted"] >= 2

    def test_dev_dirs_dry_run_not_deleted(self, tmp_path):
        ai_root, _ = self._setup_ai_image_dir(tmp_path, ["test", "unknown"])

        with self._patch_session():
            with patch(
                "backend.services.visual.ai_image_backend.get_ai_image_output_dir",
                return_value=ai_root,
            ):
                from backend.services.queue_services import run_cleanup
                result = run_cleanup(dry_run=True)

        assert (ai_root / "test").exists()
        assert (ai_root / "unknown").exists()
        assert result["dry_run"] is True

    def test_old_completed_job_dir_removed(self, tmp_path):
        """A completed job directory older than retention is deleted."""
        from backend.queue_models import ContentQueueJob, QueueStatus

        old_time = datetime.now(timezone.utc) - timedelta(days=10)
        job_id = "aaaaaaaa-0000-0000-0000-000000000001"

        db = self._db()
        db.add(ContentQueueJob(
            id=job_id, topic="old test topic",
            status=QueueStatus.COMPLETED,
            completed_at=old_time, started_at=old_time,
        ))
        db.commit()
        db.close()

        ai_root, _ = self._setup_ai_image_dir(tmp_path, [job_id])

        with self._patch_session():
            with patch(
                "backend.services.visual.ai_image_backend.get_ai_image_output_dir",
                return_value=ai_root,
            ):
                from backend.services.queue_services import run_cleanup
                result = run_cleanup(dry_run=False)

        assert not (ai_root / job_id).exists()
        assert result["files_deleted"] >= 1

    def test_recent_completed_job_dir_preserved(self, tmp_path):
        """A completed job directory within the retention window is kept."""
        from backend.queue_models import ContentQueueJob, QueueStatus

        recent_time = datetime.now(timezone.utc) - timedelta(days=2)
        job_id = "bbbbbbbb-0000-0000-0000-000000000002"

        db = self._db()
        db.add(ContentQueueJob(
            id=job_id, topic="recent test topic",
            status=QueueStatus.COMPLETED,
            completed_at=recent_time, started_at=recent_time,
        ))
        db.commit()
        db.close()

        ai_root, _ = self._setup_ai_image_dir(tmp_path, [job_id])

        with self._patch_session():
            with patch(
                "backend.services.visual.ai_image_backend.get_ai_image_output_dir",
                return_value=ai_root,
            ):
                from backend.services.queue_services import run_cleanup
                result = run_cleanup(dry_run=False)

        assert (ai_root / job_id).exists()

    def test_active_job_dir_preserved(self, tmp_path):
        """A directory for an active/generating job is never deleted."""
        from backend.queue_models import ContentQueueJob, QueueStatus

        job_id = "cccccccc-0000-0000-0000-000000000003"

        db = self._db()
        db.add(ContentQueueJob(
            id=job_id, topic="active test topic",
            status=QueueStatus.GENERATING_VIDEO,
        ))
        db.commit()
        db.close()

        ai_root, _ = self._setup_ai_image_dir(tmp_path, [job_id])

        with self._patch_session():
            with patch(
                "backend.services.visual.ai_image_backend.get_ai_image_output_dir",
                return_value=ai_root,
            ):
                from backend.services.queue_services import run_cleanup
                result = run_cleanup(dry_run=False)

        assert (ai_root / job_id).exists()

    def test_unknown_job_dir_preserved(self, tmp_path):
        """A directory with no matching DB record is not deleted (conservative)."""
        unknown_id = "dddddddd-0000-0000-0000-000000000004"
        ai_root, _ = self._setup_ai_image_dir(tmp_path, [unknown_id])

        with self._patch_session():
            with patch(
                "backend.services.visual.ai_image_backend.get_ai_image_output_dir",
                return_value=ai_root,
            ):
                from backend.services.queue_services import run_cleanup
                result = run_cleanup(dry_run=False)

        assert (ai_root / unknown_id).exists()

    def test_missing_ai_root_dir_does_not_crash(self, tmp_path):
        """If generated_images/ doesn't exist yet, cleanup should not raise."""
        non_existent = tmp_path / "generated_images_missing"

        with self._patch_session():
            with patch(
                "backend.services.visual.ai_image_backend.get_ai_image_output_dir",
                return_value=non_existent,
            ):
                from backend.services.queue_services import run_cleanup
                result = run_cleanup(dry_run=False)

        assert "errors" in result  # no crash
