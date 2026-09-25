"""
backend/services/video/pipeline_models.py — Phase 3G production pipeline contracts.

Defines:
  - ProductionStage: fine-grained stage enum for the video generation pipeline.
  - SceneSpec: typed scene contract used throughout the pipeline.
  - SceneManifestEntry: per-scene manifest record.
  - RenderManifest: lightweight record of what was produced.

These models are lightweight dataclasses (no database backing).
They do NOT duplicate existing ORM models.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Production Stage ──────────────────────────────────────────────────────────

class ProductionStage:
    """
    Fine-grained stage labels for the video production pipeline.

    These map to the actual operations executed inside _run_pipeline_sync.
    They are persisted as current_stage text in content_queue_jobs and
    as production_stage (structured) in the same table.
    """
    QUEUED              = "queued"
    RESEARCHING         = "researching"
    GENERATING_AUDIO    = "generating_audio"
    PREPARING_VISUALS   = "preparing_visuals"
    BUILDING_CLIPS      = "building_clips"
    ASSEMBLING          = "assembling"
    GENERATING_CAPTIONS = "generating_captions"
    RENDERING           = "rendering"
    GENERATING_THUMBNAIL = "generating_thumbnail"
    VERIFYING           = "verifying"
    UPLOADING           = "uploading"
    COMPLETED           = "completed"
    FAILED              = "failed"

    ALL = {
        QUEUED, RESEARCHING, GENERATING_AUDIO, PREPARING_VISUALS,
        BUILDING_CLIPS, ASSEMBLING, GENERATING_CAPTIONS, RENDERING,
        GENERATING_THUMBNAIL, VERIFYING, UPLOADING, COMPLETED, FAILED,
    }


# ── Scene Spec ────────────────────────────────────────────────────────────────

@dataclass
class SceneSpec:
    """
    Typed scene contract used throughout the video pipeline.

    Replaces loose dict usage in pipeline.py. All fields that
    the pipeline reads from a scene dict are captured here with
    correct types and defaults.

    duration_seconds priority:
      1. Use explicit value if present, finite, and >= 1.0
      2. Fall back to proportional-to-character-count distribution.
    """
    scene_number: int
    narration: str = ""
    visual_description: str = ""
    visual_prompt: Optional[str] = None

    # Background — all optional (images/solid colors work without these)
    background_type: Optional[str] = None
    background_path: Optional[str] = None
    background_color: Optional[str] = None
    background_fit: str = "cover"
    background_loop: bool = True           # meaningful only for local_video
    background_start_time: float = 0.0

    # Duration — optional; absence triggers proportional fallback
    duration_seconds: Optional[float] = None
    estimated_duration_seconds: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict, scene_number: Optional[int] = None) -> "SceneSpec":
        """Build a SceneSpec from a scene dict (as stored in scenes_json)."""
        num = scene_number if scene_number is not None else d.get("scene_number", 0)

        # Validate duration
        dur = None
        for key in ("duration_seconds", "estimated_duration_seconds"):
            raw = d.get(key)
            if raw is not None:
                try:
                    v = float(raw)
                    if math.isfinite(v) and v >= 1.0:
                        dur = v
                        break
                except (TypeError, ValueError):
                    pass

        return cls(
            scene_number=num,
            narration=d.get("narration", "") or "",
            visual_description=d.get("visual_description", "") or "",
            visual_prompt=d.get("visual_prompt"),
            background_type=d.get("background_type"),
            background_path=d.get("background_path"),
            background_color=d.get("background_color"),
            background_fit=d.get("background_fit", "cover") or "cover",
            background_loop=bool(d.get("background_loop", True)),
            background_start_time=float(d.get("background_start_time", 0.0) or 0.0),
            duration_seconds=dur,
            estimated_duration_seconds=d.get("estimated_duration_seconds"),
        )

    def to_dict(self) -> dict:
        """Serialize back to plain dict for compatibility with existing dict-based code."""
        return {
            "scene_number": self.scene_number,
            "narration": self.narration,
            "visual_description": self.visual_description,
            "visual_prompt": self.visual_prompt,
            "background_type": self.background_type,
            "background_path": self.background_path,
            "background_color": self.background_color,
            "background_fit": self.background_fit,
            "background_loop": self.background_loop,
            "background_start_time": self.background_start_time,
            "duration_seconds": self.duration_seconds,
            "estimated_duration_seconds": self.estimated_duration_seconds,
        }


# ── Render Manifest ───────────────────────────────────────────────────────────

@dataclass
class SceneManifestEntry:
    """Per-scene record in the render manifest."""
    scene_number: int
    duration_seconds: float
    background_type: Optional[str]
    background_path: Optional[str]
    clip_path: Optional[str]
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RenderManifest:
    """
    Lightweight record of what was produced in one pipeline run.

    Written to <temp_dir>/manifest.json after final encode.
    Does NOT contain media bytes or large binary data.
    """
    job_id: str
    timestamp: str                            # ISO-8601 UTC
    production_stage: str                     # last stage reached
    aspect_ratio: str
    width: int
    height: int

    # Scene summary
    scene_count: int
    scene_entries: list[SceneManifestEntry] = field(default_factory=list)

    # Audio
    narration_path: Optional[str] = None
    narration_duration_seconds: Optional[float] = None

    # Clips
    clip_paths: list[str] = field(default_factory=list)

    # Final output
    final_mp4_path: Optional[str] = None
    final_duration_seconds: Optional[float] = None
    final_file_size_bytes: Optional[int] = None
    final_sha256: Optional[str] = None

    # Fallback / error summary
    fallback_count: int = 0
    fallback_actions: list[str] = field(default_factory=list)
    error_summary: Optional[str] = None

    # Phase 3J: Thumbnail style
    thumbnail_style: str = "text_only"

    # Timing
    elapsed_seconds: Optional[float] = None

    def add_fallback(self, description: str) -> None:
        """Record a fallback action."""
        self.fallback_count += 1
        self.fallback_actions.append(description)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "timestamp": self.timestamp,
            "production_stage": self.production_stage,
            "aspect_ratio": self.aspect_ratio,
            "width": self.width,
            "height": self.height,
            "scene_count": self.scene_count,
            "scenes": [
                {
                    "scene_number": e.scene_number,
                    "duration_seconds": e.duration_seconds,
                    "background_type": e.background_type,
                    "background_path": e.background_path,
                    "clip_path": e.clip_path,
                    "fallback_used": e.fallback_used,
                    "fallback_reason": e.fallback_reason,
                    "error": e.error,
                }
                for e in self.scene_entries
            ],
            "narration_path": self.narration_path,
            "narration_duration_seconds": self.narration_duration_seconds,
            "clip_paths": self.clip_paths,
            "final_mp4_path": self.final_mp4_path,
            "final_duration_seconds": self.final_duration_seconds,
            "final_file_size_bytes": self.final_file_size_bytes,
            "final_sha256": self.final_sha256,
            "fallback_count": self.fallback_count,
            "fallback_actions": self.fallback_actions,
            "error_summary": self.error_summary,
            "thumbnail_style": self.thumbnail_style,  # Phase 3J
            "elapsed_seconds": self.elapsed_seconds,
        }

    def save(self, path: Path) -> None:
        """Write manifest to disk as JSON."""
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
