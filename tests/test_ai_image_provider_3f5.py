"""
AI Image Generation Provider Tests (Phase 3F.5).

Unit tests only — no real HTTP calls, no real AI generation.
Tests that require an actual network connection are clearly marked
and skipped by default (see conftest or run with --run-network-tests).

Test categories:
  - Backend adapter (PollinationsBackend)
  - Cache key stability
  - Dimension mapping
  - AIVisualProvider integration
  - Prompt handling (missing, empty, normal)
  - Aspect ratio handling (16:9, 9:16)
  - Cache hit / miss
  - Generation failure → fallback
  - Invalid backend configuration
  - Factory integration
  - Backward compatibility (existing providers unchanged)
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services.visual.ai_image_backend import (
    AIImageBackend,
    AIImageBackendFactory,
    ImageGenerationRequest,
    ImageGenerationResult,
    PollinationsBackend,
    aspect_ratio_to_dimensions,
    build_cache_key,
    get_ai_image_output_dir,
    get_scene_output_dir,
)
from backend.services.visual.visual_provider import (
    AIVisualProvider,
    FallbackVisualProvider,
    LocalVisualProvider,
    VisualAssetResult,
    VisualProviderFactory,
    VisualProviderType,
    generate_visual_asset,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_success_result(path: Path) -> ImageGenerationResult:
    return ImageGenerationResult(
        success=True,
        output_path=path,
        error_message=None,
        backend_name="pollinations",
        model="flux",
        generation_time_s=1.5,
        width=1280,
        height=720,
    )


def _make_failure_result(msg: str = "HTTP 503") -> ImageGenerationResult:
    return ImageGenerationResult(
        success=False,
        output_path=None,
        error_message=msg,
        backend_name="pollinations",
        model="flux",
        generation_time_s=0.5,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Dimension mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestAspectRatioToDimensions:
    def test_16_9(self):
        w, h = aspect_ratio_to_dimensions("16:9")
        assert w == 1280 and h == 720
        assert w > h  # landscape

    def test_9_16(self):
        w, h = aspect_ratio_to_dimensions("9:16")
        assert w == 720 and h == 1280
        assert h > w  # portrait

    def test_1_1(self):
        w, h = aspect_ratio_to_dimensions("1:1")
        assert w == h == 1024

    def test_unknown_falls_back_to_landscape(self):
        w, h = aspect_ratio_to_dimensions("3:7")
        assert w == 1280 and h == 720  # default

    def test_16_9_landscape_shape(self):
        w, h = aspect_ratio_to_dimensions("16:9")
        assert abs(w / h - 16 / 9) < 0.05

    def test_9_16_portrait_shape(self):
        w, h = aspect_ratio_to_dimensions("9:16")
        assert abs(h / w - 16 / 9) < 0.05


# ─────────────────────────────────────────────────────────────────────────────
# 2. Cache key stability
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildCacheKey:
    def test_returns_hex_string(self):
        key = build_cache_key("a cat", "16:9", "flux", "pollinations")
        assert isinstance(key, str)
        assert len(key) == 64  # SHA-256 hex = 64 chars

    def test_deterministic(self):
        k1 = build_cache_key("a cat", "16:9", "flux", "pollinations")
        k2 = build_cache_key("a cat", "16:9", "flux", "pollinations")
        assert k1 == k2

    def test_different_prompt_different_key(self):
        k1 = build_cache_key("a cat", "16:9", "flux", "pollinations")
        k2 = build_cache_key("a dog", "16:9", "flux", "pollinations")
        assert k1 != k2

    def test_different_aspect_ratio_different_key(self):
        k1 = build_cache_key("a cat", "16:9", "flux", "pollinations")
        k2 = build_cache_key("a cat", "9:16", "flux", "pollinations")
        assert k1 != k2

    def test_different_model_different_key(self):
        k1 = build_cache_key("a cat", "16:9", "flux", "pollinations")
        k2 = build_cache_key("a cat", "16:9", "turbo", "pollinations")
        assert k1 != k2

    def test_different_backend_different_key(self):
        k1 = build_cache_key("a cat", "16:9", "flux", "pollinations")
        k2 = build_cache_key("a cat", "16:9", "flux", "other_backend")
        assert k1 != k2

    def test_not_python_hash(self):
        # Verify it's SHA-256, not Python hash()
        raw = "v1|pollinations|flux|16:9|a cat"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert build_cache_key("a cat", "16:9", "flux", "pollinations") == expected

    def test_empty_prompt_does_not_crash(self):
        key = build_cache_key("", "16:9", "flux", "pollinations")
        assert len(key) == 64

    def test_unicode_prompt(self):
        key = build_cache_key("日本の富士山", "16:9", "flux", "pollinations")
        assert len(key) == 64


# ─────────────────────────────────────────────────────────────────────────────
# 3. Output directory helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestOutputDirectoryHelpers:
    def test_get_ai_image_output_dir_uses_env(self, tmp_path):
        with patch.dict(os.environ, {"AI_IMAGE_OUTPUT_DIR": str(tmp_path)}):
            result = get_ai_image_output_dir()
        assert result == tmp_path

    def test_get_ai_image_output_dir_falls_back_to_data_dir(self, tmp_path):
        env = {"DATA_DIR": str(tmp_path), "AI_IMAGE_OUTPUT_DIR": ""}
        with patch.dict(os.environ, env):
            result = get_ai_image_output_dir()
        assert result == tmp_path / "generated_images"

    def test_get_scene_output_dir_creates_directory(self, tmp_path):
        with patch.dict(os.environ, {"AI_IMAGE_OUTPUT_DIR": str(tmp_path)}):
            result = get_scene_output_dir("job-abc-123")
        assert result.exists()
        assert result.is_dir()
        assert result.name == "job-abc-123"

    def test_get_scene_output_dir_idempotent(self, tmp_path):
        with patch.dict(os.environ, {"AI_IMAGE_OUTPUT_DIR": str(tmp_path)}):
            r1 = get_scene_output_dir("job-xyz")
            r2 = get_scene_output_dir("job-xyz")
        assert r1 == r2


# ─────────────────────────────────────────────────────────────────────────────
# 4. PollinationsBackend URL construction
# ─────────────────────────────────────────────────────────────────────────────

class TestPollinationsBackendUrl:
    def _make_request(self, prompt="test", width=1280, height=720, model="flux",
                      output_path=None, seed=None):
        return ImageGenerationRequest(
            prompt=prompt,
            aspect_ratio="16:9",
            width=width,
            height=height,
            output_path=output_path or Path("/tmp/out.jpg"),
            model=model,
            seed=seed,
        )

    def test_url_contains_encoded_prompt(self):
        backend = PollinationsBackend()
        url = backend._build_url(self._make_request(prompt="a mountain landscape"))
        assert "a%20mountain%20landscape" in url or "a+mountain+landscape" in url or "a%20mountain" in url

    def test_url_contains_width_and_height(self):
        backend = PollinationsBackend()
        url = backend._build_url(self._make_request(width=1280, height=720))
        assert "width=1280" in url
        assert "height=720" in url

    def test_url_contains_model(self):
        backend = PollinationsBackend()
        url = backend._build_url(self._make_request(model="turbo"))
        assert "model=turbo" in url

    def test_url_contains_nologo(self):
        backend = PollinationsBackend()
        url = backend._build_url(self._make_request())
        assert "nologo=true" in url

    def test_url_contains_nofeed(self):
        backend = PollinationsBackend()
        url = backend._build_url(self._make_request())
        assert "nofeed=true" in url

    def test_url_contains_seed_when_provided(self):
        backend = PollinationsBackend()
        url = backend._build_url(self._make_request(seed=42))
        assert "seed=42" in url

    def test_url_no_seed_when_not_provided(self):
        backend = PollinationsBackend()
        url = backend._build_url(self._make_request(seed=None))
        assert "seed" not in url

    def test_url_starts_with_pollinations_base(self):
        backend = PollinationsBackend()
        url = backend._build_url(self._make_request())
        assert url.startswith("https://image.pollinations.ai/prompt/")

    def test_9_16_url_uses_portrait_dimensions(self):
        backend = PollinationsBackend()
        req = ImageGenerationRequest(
            prompt="portrait",
            aspect_ratio="9:16",
            width=720,
            height=1280,
            output_path=Path("/tmp/out.jpg"),
            model="flux",
        )
        url = backend._build_url(req)
        assert "width=720" in url
        assert "height=1280" in url


# ─────────────────────────────────────────────────────────────────────────────
# 5. PollinationsBackend generate_image (mocked HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestPollinationsBackendGenerate:
    def _make_request(self, tmp_path, prompt="test prompt"):
        return ImageGenerationRequest(
            prompt=prompt,
            aspect_ratio="16:9",
            width=1280,
            height=720,
            output_path=tmp_path / "out.jpg",
            model="flux",
            timeout_s=30.0,
        )

    def test_success_writes_file(self, tmp_path):
        backend = PollinationsBackend(max_retries=0)
        req = self._make_request(tmp_path)
        # Must be a real, valid JPEG that passes PIL.Image.verify()
        import io as _io, random as _random
        from PIL import Image as _Image
        buf = _io.BytesIO()
        pixels = bytes([_random.randint(0, 255) for _ in range(256 * 144 * 3)])
        _Image.frombytes("RGB", (256, 144), pixels).save(buf, format="JPEG", quality=85)
        fake_image_bytes = buf.getvalue()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_response.content = fake_image_bytes

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)

        assert result.success is True
        assert result.output_path == req.output_path
        assert req.output_path.read_bytes() == fake_image_bytes
        assert result.backend_name == "pollinations"
        assert result.model == "flux"
        assert result.width == 1280
        assert result.height == 720

    def test_http_error_returns_failure(self, tmp_path):
        backend = PollinationsBackend(max_retries=0)
        req = self._make_request(tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)

        assert result.success is False
        assert result.output_path is None
        assert "HTTP 503" in result.error_message or "failed" in result.error_message.lower()

    def test_timeout_returns_failure(self, tmp_path):
        import httpx as real_httpx
        backend = PollinationsBackend(max_retries=0)
        req = self._make_request(tmp_path)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = real_httpx.TimeoutException("timed out")

        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)

        assert result.success is False
        assert "Timeout" in result.error_message or "timeout" in result.error_message.lower()

    def test_wrong_content_type_retries_and_fails(self, tmp_path):
        backend = PollinationsBackend(max_retries=1)
        req = self._make_request(tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = b"<html>not an image</html>"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("backend.services.visual.ai_image_backend.httpx.Client", return_value=mock_client):
            result = backend.generate_image(req)

        assert result.success is False
        assert "content-type" in result.error_message.lower() or "failed" in result.error_message.lower()

    def test_backend_name(self):
        backend = PollinationsBackend()
        assert backend.backend_name == "pollinations"


# ─────────────────────────────────────────────────────────────────────────────
# 6. AIImageBackendFactory
# ─────────────────────────────────────────────────────────────────────────────

class TestAIImageBackendFactory:
    def test_creates_pollinations_by_default(self):
        with patch.dict(os.environ, {"AI_IMAGE_BACKEND": "pollinations"}):
            backend = AIImageBackendFactory.create()
        assert isinstance(backend, PollinationsBackend)

    def test_creates_pollinations_from_env(self):
        with patch.dict(os.environ, {"AI_IMAGE_BACKEND": "pollinations"}):
            backend = AIImageBackendFactory.create()
        assert backend.backend_name == "pollinations"

    def test_explicit_override(self):
        backend = AIImageBackendFactory.create("pollinations")
        assert isinstance(backend, PollinationsBackend)

    def test_unknown_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown AI image backend"):
            AIImageBackendFactory.create("nonexistent_backend")

    def test_model_from_env(self):
        with patch.dict(os.environ, {"AI_IMAGE_MODEL": "turbo", "AI_IMAGE_BACKEND": "pollinations"}):
            backend = AIImageBackendFactory.create()
        assert isinstance(backend, PollinationsBackend)
        assert backend._model == "turbo"

    def test_timeout_from_env(self):
        with patch.dict(os.environ, {"AI_IMAGE_TIMEOUT_S": "120", "AI_IMAGE_BACKEND": "pollinations"}):
            backend = AIImageBackendFactory.create()
        assert backend._timeout_s == 120.0

    def test_max_retries_from_env(self):
        with patch.dict(os.environ, {"AI_IMAGE_MAX_RETRIES": "5", "AI_IMAGE_BACKEND": "pollinations"}):
            backend = AIImageBackendFactory.create()
        assert backend._max_retries == 5


# ─────────────────────────────────────────────────────────────────────────────
# 7. AIVisualProvider
# ─────────────────────────────────────────────────────────────────────────────

class TestAIVisualProviderInit:
    def test_provider_name_is_ai(self):
        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = MagicMock(backend_name="pollinations")
            provider = AIVisualProvider()
        assert provider.provider_name == "ai"

    def test_uses_factory_to_create_backend(self):
        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_backend = MagicMock(backend_name="pollinations")
            mock_factory.create.return_value = mock_backend
            provider = AIVisualProvider()
        mock_factory.create.assert_called_once()
        assert provider._backend is mock_backend

    def test_backend_override(self):
        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = MagicMock(backend_name="pollinations")
            AIVisualProvider("pollinations")
        mock_factory.create.assert_called_once_with("pollinations")


class TestAIVisualProviderGenerate:
    def _make_provider(self, mock_backend):
        """Create an AIVisualProvider with a mock backend injected."""
        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = mock_backend
            provider = AIVisualProvider()
        return provider

    def test_success_returns_asset_path(self, tmp_path):
        img_file = tmp_path / "job1" / "scene_000_abc.jpg"
        img_file.parent.mkdir(parents=True)
        img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_success_result(img_file)

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_MODEL": "flux",
            "AI_IMAGE_CACHE_ENABLED": "false",  # disable cache so we always call backend
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                "a mountain scene",
                "16:9",
                {"job_id": "job1", "scene_number": 0},
            )

        assert result.asset_path is not None
        assert result.fallback_used is False
        assert result.provider_name == "ai"

    def test_failure_returns_fallback(self, tmp_path):
        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_failure_result("network error")

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                "a landscape", "16:9",
                {"job_id": "job2", "scene_number": 0},
            )

        assert result.asset_path is None
        assert result.fallback_used is True
        assert result.error_message is not None
        assert result.provider_name == "ai"

    def test_missing_prompt_uses_fallback_prompt(self, tmp_path):
        img_file = tmp_path / "j" / "s.jpg"
        img_file.parent.mkdir(parents=True)
        img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_success_result(img_file)

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                None, "16:9",
                {"job_id": "j", "scene_number": 0},
            )

        # Should have called the backend with a fallback prompt, not crashed
        mock_backend.generate_image.assert_called_once()
        called_prompt = mock_backend.generate_image.call_args[0][0].prompt
        assert called_prompt  # non-empty
        assert "16:9" in called_prompt or "cinematic" in called_prompt.lower() or "Abstract" in called_prompt

    def test_empty_prompt_uses_fallback_prompt(self, tmp_path):
        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_failure_result()

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                "   ", "16:9",
                {"job_id": "jx", "scene_number": 0},
            )

        # Should not raise, and should have tried
        mock_backend.generate_image.assert_called_once()

    def test_backend_exception_returns_fallback(self, tmp_path):
        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.side_effect = RuntimeError("backend exploded")

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                "a scene", "16:9",
                {"job_id": "j3", "scene_number": 1},
            )

        assert result.fallback_used is True
        assert "backend exploded" in result.error_message

    def test_16_9_passes_landscape_dimensions(self, tmp_path):
        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_failure_result()

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            provider.generate_visual("test", "16:9", {"job_id": "jj", "scene_number": 0})

        req = mock_backend.generate_image.call_args[0][0]
        assert req.width == 1280
        assert req.height == 720
        assert req.width > req.height

    def test_9_16_passes_portrait_dimensions(self, tmp_path):
        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_failure_result()

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            provider.generate_visual("test", "9:16", {"job_id": "jk", "scene_number": 0})

        req = mock_backend.generate_image.call_args[0][0]
        assert req.width == 720
        assert req.height == 1280
        assert req.height > req.width

    def test_no_scene_context_does_not_crash(self, tmp_path):
        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_failure_result()

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual("a scene", "16:9", None)

        assert result.provider_name == "ai"


class TestAIVisualProviderCache:
    def _make_provider(self, mock_backend):
        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = mock_backend
            provider = AIVisualProvider()
        return provider

    def test_cache_hit_skips_backend(self, tmp_path):
        from backend.services.visual.ai_image_backend import build_cache_key

        job_id = "cache-test-job"
        scene_num = 0
        model = "flux"
        backend_name = "pollinations"
        aspect = "16:9"
        prompt = "a snowy forest"

        # Create the cached file
        cache_key = build_cache_key(prompt, aspect, model, backend_name)
        out_dir = tmp_path / job_id
        out_dir.mkdir(parents=True)
        cached_file = out_dir / f"scene_{scene_num:03d}_{cache_key[:8]}.jpg"
        cached_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        mock_backend = MagicMock()
        mock_backend.backend_name = backend_name

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_MODEL": model,
            "AI_IMAGE_CACHE_ENABLED": "true",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                prompt, aspect, {"job_id": job_id, "scene_number": scene_num}
            )

        # Backend should NOT have been called
        mock_backend.generate_image.assert_not_called()
        assert result.fallback_used is False
        assert result.asset_path == str(cached_file)
        assert result.metadata.get("cache_hit") is True

    def test_cache_miss_calls_backend(self, tmp_path):
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"

        img_file = tmp_path / "miss-job" / "out.jpg"
        img_file.parent.mkdir(parents=True)
        img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        mock_backend.generate_image.return_value = _make_success_result(img_file)

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "true",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                "unique prompt xyz", "16:9",
                {"job_id": "miss-job", "scene_number": 0},
            )

        mock_backend.generate_image.assert_called_once()

    def test_cache_disabled_always_calls_backend(self, tmp_path):
        from backend.services.visual.ai_image_backend import build_cache_key

        job_id = "no-cache-job"
        model = "flux"
        prompt = "always regenerate"
        aspect = "16:9"
        backend_name = "pollinations"

        # Pre-create the cached file
        cache_key = build_cache_key(prompt, aspect, model, backend_name)
        out_dir = tmp_path / job_id
        out_dir.mkdir(parents=True)
        cached_file = out_dir / f"scene_000_{cache_key[:8]}.jpg"
        cached_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        mock_backend = MagicMock()
        mock_backend.backend_name = backend_name  # set as attribute, not constructor kwarg
        mock_backend.generate_image.return_value = _make_failure_result()

        provider = self._make_provider(mock_backend)

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_MODEL": model,
            "AI_IMAGE_CACHE_ENABLED": "false",  # disabled
        }
        with patch.dict(os.environ, env):
            provider.generate_visual(
                prompt, aspect, {"job_id": job_id, "scene_number": 0}
            )

        # Cache disabled → backend must be called even though file exists
        mock_backend.generate_image.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 8. Output file naming / structure
# ─────────────────────────────────────────────────────────────────────────────

class TestOutputFileStructure:
    def test_output_file_is_under_job_dir(self, tmp_path):
        def fake_generate(req):
            req.output_path.parent.mkdir(parents=True, exist_ok=True)
            req.output_path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
            return _make_success_result(req.output_path)

        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.side_effect = fake_generate

        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = mock_backend
            provider = AIVisualProvider()

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                "a test scene", "16:9",
                {"job_id": "structured-job", "scene_number": 3},
            )

        # Check the path passed to backend is under job_id dir
        req = mock_backend.generate_image.call_args[0][0]
        assert "structured-job" in str(req.output_path)
        assert "scene_003" in req.output_path.name

    def test_different_scenes_get_different_files(self, tmp_path):
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = _make_failure_result()

        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = mock_backend
            provider = AIVisualProvider()

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            provider.generate_visual("prompt", "16:9", {"job_id": "job-multi", "scene_number": 0})
            provider.generate_visual("prompt", "16:9", {"job_id": "job-multi", "scene_number": 1})

        calls = mock_backend.generate_image.call_args_list
        path0 = calls[0][0][0].output_path
        path1 = calls[1][0][0].output_path
        assert path0.name != path1.name  # different filenames for different scene numbers


# ─────────────────────────────────────────────────────────────────────────────
# 9. Factory integration
# ─────────────────────────────────────────────────────────────────────────────

class TestFactoryIntegration:
    def test_ai_provider_from_factory(self):
        with patch.dict(os.environ, {"VISUAL_PROVIDER": "ai"}):
            with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
                mock_factory.create.return_value = MagicMock(backend_name="pollinations")
                provider = VisualProviderFactory.get_default_provider()
        assert provider.provider_name == "ai"
        assert isinstance(provider, AIVisualProvider)

    def test_ai_provider_from_create_provider(self):
        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = MagicMock(backend_name="pollinations")
            provider = VisualProviderFactory.create_provider("ai")
        assert isinstance(provider, AIVisualProvider)

    def test_invalid_backend_in_env_raises_on_init(self):
        with patch.dict(os.environ, {"AI_IMAGE_BACKEND": "bad_backend"}):
            with pytest.raises(ValueError, match="Unknown AI image backend"):
                AIVisualProvider()

    def test_generate_visual_asset_uses_ai_when_configured(self, tmp_path):
        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_failure_result()

        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = mock_backend

            env = {
                "VISUAL_PROVIDER": "ai",
                "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
                "AI_IMAGE_CACHE_ENABLED": "false",
            }
            with patch.dict(os.environ, env):
                result = generate_visual_asset(
                    "a test",
                    "16:9",
                    {"job_id": "gva-test", "scene_number": 0},
                )

        assert result.provider_name == "ai"


# ─────────────────────────────────────────────────────────────────────────────
# 10. Backward compatibility — existing providers must be unaffected
# ─────────────────────────────────────────────────────────────────────────────

class TestBackwardCompatibility:
    def test_fallback_provider_unchanged(self):
        provider = FallbackVisualProvider()
        result = provider.generate_visual("any prompt", "16:9")
        assert result.asset_path is None
        assert result.fallback_used is True
        assert result.provider_name == "fallback"

    def test_local_provider_name(self):
        provider = LocalVisualProvider()
        assert provider.provider_name == "local"

    def test_local_provider_missing_job_id(self):
        provider = LocalVisualProvider()
        result = provider.generate_visual("prompt", "16:9", {})
        assert result.fallback_used is True

    def test_visual_provider_type_enum_unchanged(self):
        assert VisualProviderType.LOCAL.value == "local"
        assert VisualProviderType.FALLBACK.value == "fallback"
        assert VisualProviderType.AI.value == "ai"

    def test_visual_asset_result_dataclass_unchanged(self):
        r = VisualAssetResult(
            asset_path="/p/img.jpg",
            asset_type="image",
            provider_name="test",
        )
        assert r.fallback_used is False
        assert r.is_fallback is False

    def test_invalid_provider_still_raises(self):
        with pytest.raises(ValueError):
            VisualProviderFactory.create_provider("nonexistent")

    def test_factory_default_without_env_is_local(self):
        env = {k: v for k, v in os.environ.items() if k != "VISUAL_PROVIDER"}
        with patch.dict(os.environ, env, clear=True):
            # patch AIImageBackendFactory so 'ai' provider doesn't fail on init
            with patch("backend.services.visual.visual_provider.AIImageBackendFactory"):
                provider = VisualProviderFactory.get_default_provider()
        assert provider.provider_name == "local"


# ─────────────────────────────────────────────────────────────────────────────
# 11. Metadata content
# ─────────────────────────────────────────────────────────────────────────────

class TestMetadataContent:
    def test_success_metadata_contains_backend(self, tmp_path):
        img_file = tmp_path / "meta-job" / "s.jpg"
        img_file.parent.mkdir(parents=True)
        img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_success_result(img_file)

        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = mock_backend
            provider = AIVisualProvider()

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual("a scene", "16:9", {"job_id": "meta-job", "scene_number": 0})

        assert result.metadata is not None
        assert result.metadata.get("backend") == "pollinations"
        assert result.metadata.get("cache_hit") is False

    def test_success_metadata_contains_job_and_scene(self, tmp_path):
        img_file = tmp_path / "meta2-job" / "s.jpg"
        img_file.parent.mkdir(parents=True)
        img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        mock_backend = MagicMock(backend_name="pollinations")
        mock_backend.generate_image.return_value = _make_success_result(img_file)

        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as mock_factory:
            mock_factory.create.return_value = mock_backend
            provider = AIVisualProvider()

        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            result = provider.generate_visual("scene", "16:9", {"job_id": "meta2-job", "scene_number": 2})

        assert result.metadata.get("job_id") == "meta2-job"
        assert result.metadata.get("scene_number") == 2
