"""
P0.5 — Production background selection for queue jobs.

When a job has no explicit background_type (None), resolve real visuals:

  1. Relevant local images (then local videos) when assets exist
  2. Free AI image provider (Pollinations) when available
  3. Clean gradient fallback per scene (never gray placeholders)

One failed scene must not fail the whole video.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Optional
# statistics unused after solid-plate check rewrite

logger = logging.getLogger(__name__)

PRODUCTION_AUTO = "production_auto"
_MIN_BYTES = 1_500
_MIN_DIM = 320


def resolve_job_background_type(explicit: Optional[str]) -> str:
    """None/blank → production_auto. Explicit values pass through."""
    if explicit is None or not str(explicit).strip():
        return os.getenv("QUEUE_DEFAULT_BACKGROUND", PRODUCTION_AUTO).strip() or PRODUCTION_AUTO
    return str(explicit).strip().lower()


def _looks_like_decorative_abstract(rgb_img) -> bool:
    """
    P0.6: Detect seeded procedural art (thin center ring + saturated blobs).
    Prefer clean gradient over misleading decorative fillers.
    """
    import math
    import statistics

    w, h = rgb_img.size
    cx, cy = w // 2, h // 2
    step = max(2, min(w, h) // 360)

    white_dists: list[float] = []
    sat_hits = sat_total = 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = rgb_img.getpixel((x, y))
            sat_total += 1
            mx, mn = max(r, g, b), min(r, g, b)
            if mx > 140 and (mx - mn) > 90:
                sat_hits += 1
            if mn > 240 and (mx - mn) < 40:
                white_dists.append(math.hypot(x - cx, y - cy))

    sat_frac = sat_hits / max(sat_total, 1)
    if len(white_dists) < 40:
        return False

    med = statistics.median(white_dists)
    stdev = statistics.pstdev(white_dists)
    ring = sum(1 for d in white_dists if abs(d - med) < max(10.0, med * 0.06))
    ring_frac = ring / len(white_dists)
    # Thin geometric circle: whites cluster at one radius; neon blobs optional but common
    ring_ok = (
        stdev < med * 0.12
        and ring_frac >= 0.45
        and 0.18 * min(w, h) < med < 0.55 * min(w, h)
    )
    return bool(ring_ok and sat_frac >= 0.05)


def validate_image_file(path: Optional[str]) -> bool:
    """Return True if path is a usable raster image (not empty/broken/tiny/flat-gray)."""
    if not path:
        return False
    p = Path(path)
    if not p.is_file() or p.stat().st_size < _MIN_BYTES:
        return False
    try:
        from PIL import Image
    except ImportError:
        return p.stat().st_size >= _MIN_BYTES

    try:
        with Image.open(p) as img:
            img.load()
            w, h = img.size
            if w < _MIN_DIM or h < _MIN_DIM:
                return False
            # Detect near-solid gray placeholders (low chroma + low variance)
            rgb = img.convert("RGB")
            sample = rgb.resize((64, 64))
            # Reject near-solid plates (gray/cyan Phase-3F test fixtures, JPEG noise)
            extrema = sample.getextrema()  # ((rmin,rmax), ...)
            channel_ranges = [mx - mn for mn, mx in extrema]
            means = [(mn + mx) / 2.0 for mn, mx in extrema]
            chroma = max(means) - min(means)
            # Real photos have substantial range; flat fixtures stay ~0–35 even with JPEG noise
            if max(channel_ranges) < 45 or (chroma < 12 and max(channel_ranges) < 60):
                logger.warning(
                    "Rejecting flat placeholder image: %s (ranges=%s chroma=%.1f)",
                    p.name, channel_ranges, chroma,
                )
                return False
            if _looks_like_decorative_abstract(rgb):
                logger.warning(
                    "Rejecting decorative abstract filler image: %s",
                    p.name,
                )
                return False
            return True
    except Exception as exc:
        logger.warning("Image validation failed for %s: %s", p, exc)
        return False


def _tokens(text: str) -> set[str]:
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "this", "that", "from", "as", "by", "it", "be", "at",
        "scene", "image", "photo", "background", "visual", "cinematic",
    }
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in stop}


def _fuzzy_token_overlap(a: set[str], b: set[str]) -> int:
    """Exact matches plus stem prefixes (magnet/magnets/magnetic)."""
    score = len(a & b)
    for x in a:
        for y in b:
            if x == y:
                continue
            if len(x) >= 4 and len(y) >= 4 and (x.startswith(y) or y.startswith(x)):
                score += 1
    return score


def _score_asset(filename: str, scene: dict[str, Any], topic: str) -> int:
    name = Path(filename).stem.replace("_", " ").replace("-", " ")
    name_tokens = _tokens(name)
    if not name_tokens:
        return 0
    prompt_tokens = _tokens(
        " ".join(filter(None, [scene.get("visual_prompt"), scene.get("visual_description")]))
    )
    context_tokens = _tokens(" ".join(filter(None, [scene.get("narration"), topic])))
    # Prefer filename tokens that match the visual prompt over generic narration words
    return 3 * _fuzzy_token_overlap(name_tokens, prompt_tokens) + _fuzzy_token_overlap(
        name_tokens, context_tokens
    )


def _pick_local_paths(
    scenes: list[dict[str, Any]],
    *,
    job_id: str,
    aspect_ratio: str,
    topic: str,
    prefer_video: bool = False,
) -> list[Optional[str]]:
    if prefer_video:
        from backend.services.video.video_asset_discovery import select_auto_backgrounds
        return select_auto_backgrounds(job_id=job_id, scene_count=len(scenes), aspect_ratio=aspect_ratio)

    from backend.services.video.image_asset_discovery import discover_image_assets

    all_images = discover_image_assets()
    valid = []
    for img in all_images:
        if not (img.width and img.height and img.width >= _MIN_DIM and img.height >= _MIN_DIM):
            continue
        # Skip fixture folders under the project images dir (e.g. .../test_3f4/)
        try:
            from backend.services.video.background_config import IMAGES_DIR
            rel = Path(img.path).resolve().relative_to(Path(IMAGES_DIR).resolve())
            if any(
                part.lower().startswith("test_") or part.lower() in {"fixtures", "samples_test"}
                for part in rel.parts
            ):
                continue
        except Exception:
            pass
        if not validate_image_file(img.path):
            continue
        valid.append(img)
    if not valid:
        return [None] * len(scenes)

    if aspect_ratio == "16:9":
        preferred = [i for i in valid if i.orientation == "landscape"] or valid
    elif aspect_ratio == "9:16":
        preferred = [i for i in valid if i.orientation == "portrait"] or valid
    else:
        preferred = valid

    seed = int.from_bytes(hashlib.sha256(job_id.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    pool = preferred.copy()
    rng.shuffle(pool)

    used: set[str] = set()
    out: list[Optional[str]] = []
    for scene in scenes:
        ranked = sorted(
            pool,
            key=lambda img: (
                0 if img.path in used else 1,  # unused first
                _score_asset(img.filename, scene, topic),
            ),
            reverse=True,
        )
        # P0.6: only use a local image when filename tokens match the scene/topic.
        # Unrelated stock (landscape_*, abstract fillers) → None → AI/clean gradient.
        chosen = None
        if ranked:
            best = ranked[0]
            score = _score_asset(best.filename, scene, topic)
            if score >= 1 and best.path not in used:
                chosen = best.path
            elif score >= 1:
                # Best was already used — take next unused with score >= 1
                for cand in ranked:
                    if cand.path in used:
                        continue
                    if _score_asset(cand.filename, scene, topic) >= 1:
                        chosen = cand.path
                        break
        if chosen:
            used.add(chosen)
        out.append(chosen)
    return out


def apply_production_backgrounds(
    *,
    job_id: str,
    scenes: list[dict[str, Any]],
    aspect_ratio: str,
    topic: str = "",
    visual_style: str = "cinematic",
    background_color: Optional[str] = None,
    background_fit: str = "cover",
) -> dict[str, int]:
    """
    Mutate scenes in-place with production backgrounds.

    Returns counts: local_image, local_video, ai, gradient.
    """
    from backend.services.video.background_config import BackgroundType

    counts = {"local_image": 0, "local_video": 0, "ai": 0, "gradient": 0}
    if not scenes:
        return counts

    # ── 1) Local images (validated, non-flat) ─────────────────────────────
    local_paths = _pick_local_paths(
        scenes, job_id=job_id, aspect_ratio=aspect_ratio, topic=topic, prefer_video=False,
    )
    use_local_images = sum(1 for p in local_paths if p) > 0

    # Local videos discovered up front but applied only after AI fails.
    # Random stock clips are often irrelevant and can destabilize duration.
    video_paths = _pick_local_paths(
        scenes, job_id=job_id, aspect_ratio=aspect_ratio, topic=topic, prefer_video=True,
    )

    # ── 2) AI for gaps (free Pollinations) ────────────────────────────────
    ai_enabled = os.getenv("PRODUCTION_BG_AI", "true").lower() not in ("0", "false", "no", "off")
    ai_timeout = float(os.getenv("AI_IMAGE_JOB_TIMEOUT_S", "600"))
    ai_deadline = time.monotonic() + ai_timeout

    for i, scene in enumerate(scenes):
        # Respect explicit per-scene backgrounds (non-auto)
        existing = (scene.get("background_type") or "").strip().lower()
        if existing and existing not in (
            "",
            PRODUCTION_AUTO,
            BackgroundType.LOCAL_IMAGE_AUTO.value,
            BackgroundType.LOCAL_VIDEO_AUTO.value,
            BackgroundType.VISUAL_PROVIDER.value,
            BackgroundType.PLACEHOLDER.value,
        ):
            if existing == BackgroundType.PLACEHOLDER.value:
                # Never keep gray placeholders in production
                pass
            else:
                continue

        path = local_paths[i] if use_local_images else None
        if path and validate_image_file(path):
            scene["background_type"] = BackgroundType.LOCAL_IMAGE.value
            scene["background_path"] = path
            scene["background_color"] = background_color
            scene["background_fit"] = background_fit or "cover"
            counts["local_image"] += 1
            continue

        # AI attempt (before unrelated local videos)
        if ai_enabled:
            try:
                from backend.services.visual.visual_provider import generate_visual_asset

                prompt = (
                    scene.get("visual_prompt")
                    or scene.get("visual_description")
                    or None
                )
                result = generate_visual_asset(
                    visual_prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    scene_context={
                        "job_id": job_id,
                        "scene_number": i,
                        "job_ai_deadline": ai_deadline,
                        "topic": topic,
                        "visual_style": visual_style,
                        "total_scenes": len(scenes),
                    },
                    provider_type="ai",
                )
                if (
                    result
                    and result.asset_path
                    and not result.fallback_used
                    and validate_image_file(result.asset_path)
                ):
                    scene["background_type"] = BackgroundType.LOCAL_IMAGE.value
                    scene["background_path"] = result.asset_path
                    scene["background_color"] = background_color
                    scene["background_fit"] = background_fit or "cover"
                    counts["ai"] += 1
                    continue
                if result and result.error_message:
                    logger.warning(
                        "[production_bg %s] scene %d AI fallback: %s",
                        job_id, i + 1, result.error_message,
                    )
            except Exception as exc:
                logger.warning(
                    "[production_bg %s] scene %d AI error: %s",
                    job_id, i + 1, exc,
                )

        # ── 3) Local video (opt-in) — stock UHD clips often mismatch topic
        # and can destabilize final duration vs narration.
        allow_video = os.getenv("PRODUCTION_BG_ALLOW_VIDEO", "false").lower() in (
            "1", "true", "yes", "on",
        )
        vpath = video_paths[i] if i < len(video_paths) else None
        if (
            allow_video
            and vpath
            and Path(vpath).is_file()
            and Path(vpath).stat().st_size > 0
        ):
            scene["background_type"] = BackgroundType.LOCAL_VIDEO.value
            scene["background_path"] = vpath
            scene["background_color"] = background_color
            scene["background_fit"] = background_fit or "cover"
            counts["local_video"] += 1
            continue

        # ── 4) Clean gradient (never placeholder / gray plate) ────────────
        scene["background_type"] = BackgroundType.GRADIENT.value
        scene["background_path"] = None
        scene["background_color"] = background_color
        scene["background_fit"] = background_fit or "cover"
        counts["gradient"] += 1

    logger.info(
        "[production_bg %s] resolved %d scenes: local_image=%d local_video=%d ai=%d gradient=%d",
        job_id, len(scenes), counts["local_image"], counts["local_video"],
        counts["ai"], counts["gradient"],
    )
    return counts
