"""
Visual Provider Abstraction (Phase 3F.3 / 3F.5).

Provides a clean interface between visual prompts and visual assets used by the video pipeline.

Architecture:
    visual_prompt → Visual Provider → VisualAssetResult → existing FFmpeg renderer

Providers:
    - local:    Uses local images (reuses Phase 3F.1 logic)
    - fallback: Always returns gradient/fallback result
    - ai:       AI image generation via pluggable backend (Phase 3F.5)
                Default backend: Pollinations.ai (free, no API key)
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from backend.services.visual.ai_image_backend import AIImageBackendFactory

logger = logging.getLogger(__name__)


class VisualProviderType(str, Enum):
    """Supported visual provider types."""
    LOCAL = "local"  # Local image provider (default)
    FALLBACK = "fallback"  # Gradient/fallback provider
    AI = "ai"  # AI image generation (stub for future)


@dataclass
class VisualAssetResult:
    """Result from a visual provider."""
    asset_path: Optional[str]  # Path to the asset file, or None for fallback
    asset_type: str  # "image" or "video"
    provider_name: str  # Name of the provider that generated this result
    metadata: Optional[dict] = None  # Optional provider-specific metadata
    fallback_used: bool = False  # Whether fallback was used
    error_message: Optional[str] = None  # Error message if generation failed

    @property
    def is_fallback(self) -> bool:
        """Check if this result indicates fallback to gradient/background."""
        return self.asset_path is None or self.fallback_used


class VisualProvider(ABC):
    """
    Abstract base class for visual providers.

    All visual providers must implement this interface.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name."""
        ...

    @abstractmethod
    def generate_visual(
        self,
        visual_prompt: Optional[str],
        aspect_ratio: str,
        scene_context: Optional[dict] = None,
    ) -> VisualAssetResult:
        """
        Generate a visual asset from a visual prompt.

        Args:
            visual_prompt: The visual prompt text (may be None/empty)
            aspect_ratio: Target aspect ratio (16:9 or 9:16)
            scene_context: Optional scene context (scene_number, job_id, etc.)

        Returns:
            VisualAssetResult with asset path or fallback indication.
        """
        ...


class LocalVisualProvider(VisualProvider):
    """
    Local image provider using Phase 3F.1 auto-selection logic.

    Reuses existing deterministic aspect-aware image selection.
    """

    def __init__(self):
        self._provider_name = "local"

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def generate_visual(
        self,
        visual_prompt: Optional[str],
        aspect_ratio: str,
        scene_context: Optional[dict] = None,
    ) -> VisualAssetResult:
        """
        Select a local image using deterministic auto-selection.

        Args:
            visual_prompt: Ignored for local provider (uses deterministic selection)
            aspect_ratio: Target aspect ratio (16:9 or 9:16)
            scene_context: Must contain 'job_id' and 'scene_number' for deterministic selection

        Returns:
            VisualAssetResult with selected image path or fallback if no images available.
        """
        try:
            from backend.services.video.image_asset_discovery import select_auto_images

            # Extract required context
            job_id = scene_context.get("job_id") if scene_context else None
            scene_number = scene_context.get("scene_number", 0) if scene_context else 0

            if not job_id:
                logger.warning("Local provider requires job_id in scene_context, using fallback")
                return VisualAssetResult(
                    asset_path=None,
                    asset_type="image",
                    provider_name=self.provider_name,
                    fallback_used=True,
                    error_message="Missing job_id in scene_context",
                )

            # Select image for this specific scene
            # We request selection for scene_number+1 scenes, then take the scene_number-th result
            scene_count = scene_number + 1
            selections = select_auto_images(job_id, scene_count, aspect_ratio)

            if not selections or scene_number >= len(selections):
                logger.warning(f"No image selected for scene {scene_number}, using fallback")
                return VisualAssetResult(
                    asset_path=None,
                    asset_type="image",
                    provider_name=self.provider_name,
                    fallback_used=True,
                    error_message="No valid images available",
                )

            asset_path = selections[scene_number]

            if asset_path is None:
                logger.info(f"Scene {scene_number} using gradient fallback (no images)")
                return VisualAssetResult(
                    asset_path=None,
                    asset_type="image",
                    provider_name=self.provider_name,
                    fallback_used=True,
                    error_message="No valid images available",
                )

            logger.debug(f"Local provider selected: {asset_path} for scene {scene_number}")
            return VisualAssetResult(
                asset_path=asset_path,
                asset_type="image",
                provider_name=self.provider_name,
                fallback_used=False,
                metadata={"job_id": job_id, "scene_number": scene_number},
            )

        except Exception as e:
            logger.error(f"Local provider error: {e}, using fallback")
            return VisualAssetResult(
                asset_path=None,
                asset_type="image",
                provider_name=self.provider_name,
                fallback_used=True,
                error_message=str(e),
            )


class FallbackVisualProvider(VisualProvider):
    """
    Fallback provider that always returns gradient/background result.

    Used when no visual asset is available or when visual generation fails.
    """

    def __init__(self):
        self._provider_name = "fallback"

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def generate_visual(
        self,
        visual_prompt: Optional[str],
        aspect_ratio: str,
        scene_context: Optional[dict] = None,
    ) -> VisualAssetResult:
        """
        Always return fallback result (gradient background).

        Args:
            visual_prompt: Ignored
            aspect_ratio: Ignored
            scene_context: Ignored

        Returns:
            VisualAssetResult with None asset_path (indicates fallback).
        """
        logger.debug("Fallback provider returning gradient result")
        return VisualAssetResult(
            asset_path=None,
            asset_type="image",
            provider_name=self.provider_name,
            fallback_used=True,
        )


class AIVisualProvider(VisualProvider):
    """
    AI image generation provider (Phase 3F.5).

    Generates images using a pluggable backend adapter.
    Default backend: Pollinations.ai (free, no API key, no registration).

    Features:
      - SHA-256 prompt/aspect-ratio cache — avoids re-generating identical images
      - Per-job output directory: data/generated_images/<job_id>/scene_NNN.jpg
      - Graceful fallback to gradient on any failure (never crashes the queue)
      - Configurable via environment variables (see ai_image_backend.py)

    Environment variables:
        AI_IMAGE_BACKEND        Backend name: "pollinations" (default)
        AI_IMAGE_MODEL          Model: "flux" (default), "turbo", "stable-diffusion"
        AI_IMAGE_TIMEOUT_S      Timeout per generation attempt in seconds (default: 90)
        AI_IMAGE_OUTPUT_DIR     Root dir for generated images (default: DATA_DIR/generated_images)
        AI_IMAGE_CACHE_ENABLED  "true" (default) / "false"
        AI_IMAGE_MAX_RETRIES    Retry attempts on transient failure (default: 2)
    """

    def __init__(self, backend_name: Optional[str] = None) -> None:
        """
        Args:
            backend_name: Override backend (reads AI_IMAGE_BACKEND env var if None).
        """
        self._backend = AIImageBackendFactory.create(backend_name)
        # Note: cache_enabled is re-read at generate_visual() call time so that
        # tests and runtime config changes take effect without reinitializing the provider.

    @property
    def provider_name(self) -> str:
        return "ai"

    def generate_visual(
        self,
        visual_prompt: Optional[str],
        aspect_ratio: str,
        scene_context: Optional[dict] = None,
    ) -> VisualAssetResult:
        """
        Generate an AI image for the given prompt and aspect ratio.

        Falls back to gradient on any failure — never raises.

        Args:
            visual_prompt: Text prompt describing the desired image.
            aspect_ratio:  "16:9" or "9:16"
            scene_context: Must contain 'job_id'; optionally 'scene_number'.

        Returns:
            VisualAssetResult with local image path on success, fallback on failure.
        """
        from backend.services.visual.ai_image_backend import (
            ImageGenerationRequest,
            aspect_ratio_to_dimensions,
            build_cache_key,
            get_scene_output_dir,
        )
        job_id      = (scene_context or {}).get("job_id", "unknown")
        scene_num   = (scene_context or {}).get("scene_number", 0)
        model       = os.getenv("AI_IMAGE_MODEL", "flux").strip()

        # ── Per-job budget check ───────────────────────────────────────────
        # The queue processor passes 'job_ai_deadline' (a monotonic timestamp)
        # in scene_context when AI_IMAGE_JOB_TIMEOUT_S is configured.
        # If the deadline has passed, immediately return fallback so remaining
        # scenes don't add more latency.
        job_ai_deadline: Optional[float] = (scene_context or {}).get("job_ai_deadline")
        if job_ai_deadline is not None and time.monotonic() >= job_ai_deadline:
            logger.warning(
                "[AI provider] job=%s scene=%d: job AI budget exhausted, using gradient fallback",
                job_id, scene_num,
            )
            return VisualAssetResult(
                asset_path=None,
                asset_type="image",
                provider_name=self.provider_name,
                fallback_used=True,
                error_message="AI generation budget exhausted for this job",
            )

        # ── Handle missing/empty prompt ────────────────────────────────────
        if not visual_prompt or not visual_prompt.strip():
            logger.warning(
                "[AI provider] job=%s scene=%d: no visual_prompt, "
                "using generic fallback prompt",
                job_id, scene_num,
            )
            visual_prompt = f"Abstract background, {aspect_ratio} composition, cinematic"

        width, height = aspect_ratio_to_dimensions(aspect_ratio)

        # ── Cache check ────────────────────────────────────────────────────
        cache_key = build_cache_key(visual_prompt, aspect_ratio, model, self._backend.backend_name)
        output_dir = get_scene_output_dir(job_id)
        # Primary filename: scene_NNN_<first8 of cache>.jpg
        scene_filename = f"scene_{scene_num:03d}_{cache_key[:8]}.jpg"
        output_path = output_dir / scene_filename

        if os.getenv("AI_IMAGE_CACHE_ENABLED", "true").lower() not in ("false", "0", "no", "off") \
                and output_path.exists() and output_path.stat().st_size > 0:
            logger.info(
                "[AI provider] job=%s scene=%d: cache hit → %s",
                job_id, scene_num, output_path.name,
            )
            return VisualAssetResult(
                asset_path=str(output_path),
                asset_type="image",
                provider_name=self.provider_name,
                fallback_used=False,
                metadata={
                    "backend": self._backend.backend_name,
                    "model": model,
                    "cache_hit": True,
                    "job_id": job_id,
                    "scene_number": scene_num,
                },
            )

        # ── Generate ───────────────────────────────────────────────────────
        logger.info(
            "[AI provider] job=%s scene=%d: generating %dx%d via %s/%s",
            job_id, scene_num, width, height, self._backend.backend_name, model,
        )

        timeout_s = float(os.getenv("AI_IMAGE_TIMEOUT_S", "90"))
        request = ImageGenerationRequest(
            prompt=visual_prompt,
            aspect_ratio=aspect_ratio,
            width=width,
            height=height,
            output_path=output_path,
            model=model,
            timeout_s=timeout_s,
        )

        try:
            result = self._backend.generate_image(request)
        except Exception as exc:
            # Catch anything the backend forgot to handle
            logger.error(
                "[AI provider] job=%s scene=%d: backend raised unexpectedly: %s",
                job_id, scene_num, exc,
            )
            return VisualAssetResult(
                asset_path=None,
                asset_type="image",
                provider_name=self.provider_name,
                fallback_used=True,
                error_message=f"Backend exception: {exc}",
            )

        if result.success and result.output_path and result.output_path.exists():
            logger.info(
                "[AI provider] job=%s scene=%d: generated %s (%.1fs)",
                job_id, scene_num, result.output_path.name, result.generation_time_s,
            )
            return VisualAssetResult(
                asset_path=str(result.output_path),
                asset_type="image",
                provider_name=self.provider_name,
                fallback_used=False,
                metadata={
                    "backend": result.backend_name,
                    "model": result.model,
                    "cache_hit": False,
                    "generation_time_s": result.generation_time_s,
                    "width": result.width,
                    "height": result.height,
                    "job_id": job_id,
                    "scene_number": scene_num,
                },
            )

        # Generation failed — fall back to gradient
        error = result.error_message or "unknown generation error"
        logger.warning(
            "[AI provider] job=%s scene=%d: generation failed (%s), using gradient fallback",
            job_id, scene_num, error,
        )
        return VisualAssetResult(
            asset_path=None,
            asset_type="image",
            provider_name=self.provider_name,
            fallback_used=True,
            error_message=error,
        )


class VisualProviderFactory:
    """
    Factory for creating visual provider instances.

    Supports configuration via environment variable VISUAL_PROVIDER.
    """

    @staticmethod
    def create_provider(provider_type: str) -> VisualProvider:
        """
        Create a visual provider instance.

        Args:
            provider_type: Provider type (local, fallback, ai)

        Returns:
            VisualProvider instance.

        Raises:
            ValueError: If provider_type is invalid.
        """
        provider_type = provider_type.lower()

        if provider_type == VisualProviderType.LOCAL.value:
            return LocalVisualProvider()
        elif provider_type == VisualProviderType.FALLBACK.value:
            return FallbackVisualProvider()
        elif provider_type == VisualProviderType.AI.value:
            return AIVisualProvider()
        else:
            raise ValueError(f"Invalid visual provider type: {provider_type}")

    @staticmethod
    def get_default_provider() -> VisualProvider:
        """
        Get the default visual provider based on environment configuration.

        Falls back to LOCAL provider if VISUAL_PROVIDER is not set.

        Returns:
            VisualProvider instance.
        """
        provider_type = os.getenv("VISUAL_PROVIDER", VisualProviderType.LOCAL.value)
        return VisualProviderFactory.create_provider(provider_type)


def generate_visual_asset(
    visual_prompt: Optional[str],
    aspect_ratio: str,
    scene_context: Optional[dict] = None,
    provider_type: Optional[str] = None,
) -> VisualAssetResult:
    """
    Convenience function to generate a visual asset using the configured provider.

    Args:
        visual_prompt: The visual prompt text
        aspect_ratio: Target aspect ratio (16:9 or 9:16)
        scene_context: Optional scene context (job_id, scene_number, etc.)
        provider_type: Optional provider type override (uses default if not specified)

    Returns:
        VisualAssetResult with asset path or fallback indication.
    """
    if provider_type:
        provider = VisualProviderFactory.create_provider(provider_type)
    else:
        provider = VisualProviderFactory.get_default_provider()

    return provider.generate_visual(visual_prompt, aspect_ratio, scene_context)
