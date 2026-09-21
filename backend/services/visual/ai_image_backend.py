"""
AI Image Generation Backend Adapter (Phase 3F.5).

Provides a clean backend interface between AIVisualProvider and the actual
image-generation engine. The current implementation uses the Pollinations.ai
public API — free, no API key, no registration, no credit card required.

Architecture:
    AIVisualProvider
        └── AIImageBackend (this module)
                └── PollinationsBackend   ← default free backend
                └── (future: LocalDiffusionBackend, etc.)

Adding a new backend:
    1. Subclass AIImageBackend.
    2. Implement generate_image().
    3. Add its name to AIImageBackendFactory.

Environment variables (all optional):
    AI_IMAGE_BACKEND         Backend to use: "pollinations" (default)
    AI_IMAGE_MODEL           Model name for the backend (default: "flux")
    AI_IMAGE_TIMEOUT_S       HTTP timeout in seconds (default: 90)
    AI_IMAGE_OUTPUT_DIR      Output directory (default: DATA_DIR/generated_images)
    AI_IMAGE_CACHE_ENABLED   Enable SHA-256 prompt caching: "true" (default) / "false"
    AI_IMAGE_MAX_RETRIES     Retry attempts on transient failure (default: 2)
    AI_IMAGE_MIN_BYTES       Minimum valid image size in bytes (default: 1024)
    AI_IMAGE_JOB_TIMEOUT_S   Total AI-generation budget per queue job in seconds (default: 600)
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx  # imported at module level so tests can patch ai_image_backend.httpx

logger = logging.getLogger(__name__)

# ── Aspect ratio → pixel dimensions ──────────────────────────────────────────

_DIMENSION_MAP: dict[str, tuple[int, int]] = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1":  (1024, 1024),
    "4:3":  (1024, 768),
    "3:4":  (768, 1024),
}
_DEFAULT_DIMENSIONS = (1280, 720)  # fallback for unknown ratios


def aspect_ratio_to_dimensions(aspect_ratio: str) -> tuple[int, int]:
    """Return (width, height) pixel dimensions for a given aspect ratio string."""
    return _DIMENSION_MAP.get(aspect_ratio, _DEFAULT_DIMENSIONS)


# ── Output directory helper ───────────────────────────────────────────────────

def get_ai_image_output_dir() -> Path:
    """
    Return the root directory for AI-generated images.

    Reads AI_IMAGE_OUTPUT_DIR first; falls back to DATA_DIR/generated_images.
    """
    explicit = os.getenv("AI_IMAGE_OUTPUT_DIR", "").strip()
    if explicit:
        return Path(explicit)
    data_dir = os.getenv("DATA_DIR", "G:/youtube-uploader/data").strip()
    return Path(data_dir) / "generated_images"


def get_scene_output_dir(job_id: str) -> Path:
    """Return the per-job subdirectory for AI images, creating it if needed."""
    d = get_ai_image_output_dir() / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Cache key ─────────────────────────────────────────────────────────────────

# Version bump this string whenever the generation pipeline changes in a way
# that makes previously cached results invalid.
_CACHE_VERSION = "v1"


def build_cache_key(
    prompt: str,
    aspect_ratio: str,
    model: str,
    backend: str,
) -> str:
    """
    Build a stable SHA-256 cache key from generation inputs.

    Does NOT use Python's built-in hash() — SHA-256 is stable across runs.
    """
    raw = f"{_CACHE_VERSION}|{backend}|{model}|{aspect_ratio}|{prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Image validation ──────────────────────────────────────────────────────────

# Default minimum byte size considered a valid image.
# A 1×1 JPEG is ~620 bytes; anything under 1 KB is almost certainly invalid.
_DEFAULT_MIN_BYTES = 1024


def get_min_image_bytes() -> int:
    """Return the configured minimum valid image size in bytes."""
    try:
        return int(os.getenv("AI_IMAGE_MIN_BYTES", str(_DEFAULT_MIN_BYTES)))
    except (ValueError, TypeError):
        return _DEFAULT_MIN_BYTES


def validate_image_bytes(data: bytes) -> tuple[bool, str]:
    """
    Validate raw image bytes before writing to disk.

    Checks:
      1. Minimum byte-size threshold (AI_IMAGE_MIN_BYTES, default 1024).
      2. PIL Image.verify() — confirms the bytes are a decodable image.

    Note: Image.verify() must be called on a freshly opened image object;
    it consumes the object. We re-open from bytes using BytesIO.

    Returns:
        (valid: bool, reason: str)
    """
    min_bytes = get_min_image_bytes()
    if len(data) < min_bytes:
        return False, f"Image too small: {len(data)} bytes (minimum {min_bytes})"

    try:
        from PIL import Image
        from io import BytesIO
        img = Image.open(BytesIO(data))
        img.verify()  # raises if corrupt/truncated
        return True, "ok"
    except Exception as exc:
        return False, f"PIL verification failed: {exc}"


# ── Abstract backend interface ────────────────────────────────────────────────

@dataclass
class ImageGenerationRequest:
    """Everything a backend needs to generate one image."""
    prompt: str
    aspect_ratio: str          # e.g. "16:9"
    width: int
    height: int
    output_path: Path          # where to write the final file
    model: str
    seed: Optional[int] = None
    timeout_s: float = 90.0


@dataclass
class ImageGenerationResult:
    """Result from a backend generate_image() call."""
    success: bool
    output_path: Optional[Path]   # populated on success
    error_message: Optional[str]  # populated on failure
    backend_name: str
    model: str
    generation_time_s: float = 0.0
    width: int = 0
    height: int = 0


class AIImageBackend(ABC):
    """Abstract base class for AI image generation backends."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Short identifier for this backend, e.g. 'pollinations'."""
        ...

    @abstractmethod
    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """
        Generate an image and write it to request.output_path.

        Must be synchronous (called from the queue worker thread).
        Must NOT raise — return ImageGenerationResult(success=False, ...) on error.
        """
        ...

    def is_available(self) -> tuple[bool, str]:
        """
        Check whether this backend is reachable/configured.
        Returns (available, reason). Default: assume available.
        """
        return True, "ok"


# ── Pollinations backend ──────────────────────────────────────────────────────

_POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"

# Models known to work well on Pollinations; "flux" is highest quality.
POLLINATIONS_MODELS = ("flux", "turbo", "stable-diffusion")

# Maximum seconds to honour from a Retry-After header (avoid unbounded waits).
_MAX_RETRY_AFTER_S = 30


class PollinationsBackend(AIImageBackend):
    """
    Image generation using the free Pollinations.ai public API.

    - No API key required.
    - No registration required.
    - No credit card required.
    - Images are generated server-side; this machine only downloads the result.
    - Supports arbitrary width × height.
    - Rate-limited by Pollinations per IP; no hard quota for reasonable use.

    API format:
        GET https://image.pollinations.ai/prompt/{encoded_prompt}
            ?width=W&height=H&model=MODEL&seed=SEED&nologo=true

    Reference: Content was rephrased for compliance with licensing restrictions
    based on https://github.com/pollinations/pollinations APIDOCS.
    """

    def __init__(
        self,
        model: str = "flux",
        timeout_s: float = 90.0,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        self._timeout_s = timeout_s
        self._max_retries = max_retries

    @property
    def backend_name(self) -> str:
        return "pollinations"

    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """
        Download a generated image from Pollinations into request.output_path.

        Validation pipeline:
          HTTP 200 → content-type contains "image"
          → minimum byte-size check (AI_IMAGE_MIN_BYTES)
          → PIL Image.verify() decodability check
          → write to output_path (only on full pass)

        On any validation failure the partial/invalid file is deleted and
        ImageGenerationResult(success=False) is returned so the provider
        falls back to gradient without caching the bad file.
        """
        t0 = time.monotonic()
        url = self._build_url(request)
        logger.info(
            "Pollinations request: model=%s %dx%d prompt=%r…",
            request.model, request.width, request.height,
            request.prompt[:60],
        )

        last_error: str = "unknown error"
        for attempt in range(1, self._max_retries + 2):  # +2 so max_retries=2 → 3 attempts
            try:
                with httpx.Client(timeout=request.timeout_s, follow_redirects=True) as client:
                    resp = client.get(url)

                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "")
                    if "image" not in content_type:
                        last_error = f"Unexpected content-type: {content_type!r}"
                        logger.warning("Pollinations attempt %d: %s", attempt, last_error)
                        continue

                    # ── Image validation before committing to disk ──────────
                    image_data = resp.content
                    valid, reason = validate_image_bytes(image_data)
                    if not valid:
                        last_error = f"Image validation failed: {reason}"
                        logger.warning(
                            "Pollinations attempt %d: %s (%d bytes received)",
                            attempt, last_error, len(image_data),
                        )
                        # Do NOT write invalid bytes to disk — try again
                        continue

                    # Validation passed — write to output path
                    request.output_path.parent.mkdir(parents=True, exist_ok=True)
                    request.output_path.write_bytes(image_data)

                    elapsed = time.monotonic() - t0
                    logger.info(
                        "Pollinations OK: %s (%.1fs, %d bytes)",
                        request.output_path.name, elapsed, len(image_data),
                    )
                    return ImageGenerationResult(
                        success=True,
                        output_path=request.output_path,
                        error_message=None,
                        backend_name=self.backend_name,
                        model=request.model,
                        generation_time_s=elapsed,
                        width=request.width,
                        height=request.height,
                    )

                elif resp.status_code == 429:
                    # ── Honour Retry-After header when present ──────────────
                    retry_after = self._parse_retry_after(resp.headers)
                    last_error = f"Rate limited (HTTP 429)"
                    logger.warning(
                        "Pollinations attempt %d: rate limited, waiting %ds (Retry-After: %s)",
                        attempt, retry_after,
                        resp.headers.get("retry-after", "absent"),
                    )
                    time.sleep(retry_after)

                else:
                    last_error = f"HTTP {resp.status_code}"
                    logger.warning("Pollinations attempt %d: %s", attempt, last_error)
                    if resp.status_code >= 500:
                        time.sleep(3)  # brief back-off on server errors

            except httpx.TimeoutException:
                last_error = f"Timeout after {request.timeout_s}s"
                logger.warning("Pollinations attempt %d: %s", attempt, last_error)
            except httpx.RequestError as exc:
                last_error = f"Network error: {type(exc).__name__}"
                logger.warning("Pollinations attempt %d: %s", attempt, last_error)
            except OSError as exc:
                last_error = f"File write error: {exc}"
                logger.error("Pollinations attempt %d: %s", attempt, last_error)
                # Clean up any partial file that may have been written
                self._safe_delete(request.output_path)
                break  # filesystem errors won't get better on retry

        elapsed = time.monotonic() - t0
        return ImageGenerationResult(
            success=False,
            output_path=None,
            error_message=f"Pollinations failed after {self._max_retries + 1} attempt(s): {last_error}",
            backend_name=self.backend_name,
            model=request.model,
            generation_time_s=elapsed,
        )

    def is_available(self) -> tuple[bool, str]:
        """Quick HEAD check to see if Pollinations is reachable."""
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.head(f"{_POLLINATIONS_BASE}/test")
            return True, f"HTTP {r.status_code}"
        except Exception as exc:
            return False, str(exc)

    # ── internal ──────────────────────────────────────────────────────────────

    def _build_url(self, request: ImageGenerationRequest) -> str:
        encoded_prompt = urllib.parse.quote(request.prompt, safe="")
        params: dict[str, str | int] = {
            "width":  request.width,
            "height": request.height,
            "model":  request.model,
            "nologo": "true",
            "nofeed": "true",   # don't add to public feed
        }
        if request.seed is not None:
            params["seed"] = request.seed

        query = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
        return f"{_POLLINATIONS_BASE}/{encoded_prompt}?{query}"

    @staticmethod
    def _parse_retry_after(headers) -> int:
        """
        Parse the Retry-After header into seconds to sleep.

        Supports both numeric (seconds) and HTTP-date formats.
        Caps at _MAX_RETRY_AFTER_S to prevent unbounded waits.
        Falls back to 5s if header is absent or unparseable.
        """
        raw = headers.get("retry-after", "").strip()
        if not raw:
            return 5  # default fallback

        # Numeric form: "Retry-After: 30"
        try:
            seconds = int(raw)
            return min(max(seconds, 1), _MAX_RETRY_AFTER_S)
        except ValueError:
            pass

        # HTTP-date form: "Retry-After: Wed, 21 Oct 2015 07:28:00 GMT"
        try:
            from email.utils import parsedate_to_datetime
            retry_dt = parsedate_to_datetime(raw)
            import datetime as dt_mod
            now = dt_mod.datetime.now(dt_mod.timezone.utc)
            seconds = int((retry_dt - now).total_seconds())
            return min(max(seconds, 1), _MAX_RETRY_AFTER_S)
        except Exception:
            pass

        return 5  # unparseable — use default

    @staticmethod
    def _safe_delete(path: Optional[Path]) -> None:
        """Delete a file without raising if it doesn't exist."""
        if path and path.exists():
            try:
                path.unlink()
            except OSError:
                pass


# ── Factory ───────────────────────────────────────────────────────────────────

class AIImageBackendFactory:
    """
    Creates AI image backend instances from environment configuration.

    Environment variables:
        AI_IMAGE_BACKEND    "pollinations" (default)
        AI_IMAGE_MODEL      Model name passed to the backend (default: "flux")
        AI_IMAGE_TIMEOUT_S  HTTP/generation timeout in seconds (default: 90)
        AI_IMAGE_MAX_RETRIES Number of retry attempts on transient failure (default: 2)
    """

    @staticmethod
    def create(backend_name: Optional[str] = None) -> AIImageBackend:
        """
        Create a backend instance.

        Args:
            backend_name: Override backend name (reads AI_IMAGE_BACKEND env var if None).

        Raises:
            ValueError: If backend_name is unrecognised.
        """
        name = (backend_name or os.getenv("AI_IMAGE_BACKEND", "pollinations")).lower().strip()
        model = os.getenv("AI_IMAGE_MODEL", "flux").strip()
        timeout_s = float(os.getenv("AI_IMAGE_TIMEOUT_S", "90"))
        max_retries = int(os.getenv("AI_IMAGE_MAX_RETRIES", "2"))

        if name == "pollinations":
            return PollinationsBackend(
                model=model,
                timeout_s=timeout_s,
                max_retries=max_retries,
            )
        else:
            raise ValueError(
                f"Unknown AI image backend: {name!r}. "
                "Supported: 'pollinations'."
            )
