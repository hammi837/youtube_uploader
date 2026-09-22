"""
backend/services/visual_prompt_generator.py — Visual prompt generation (Phase 3F.2 / 3F.6).

Phase 3F.6 improvements:
  - Scene context includes visual_description, scene_number, total_scenes, topic, tone
  - Consistent job-level visual style derived from tone
  - Scene-to-scene continuity via lightweight previous-prompt context
  - Deterministic shot-type variation per scene position
  - Aspect-ratio-aware composition hints
  - Maximum prompt length control (AI_VISUAL_PROMPT_MAX_CHARS env var)

Environment variables:
    AI_VISUAL_PROMPT_MAX_CHARS   Max characters for visual prompts (default: 300)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from backend.content_models import Scene
from backend.services.llm.base import LLMError, LLMJSONError
from backend.services.llm.factory import get_llm_provider

logger = logging.getLogger(__name__)


# ── Visual style mapping ──────────────────────────────────────────────────────

# Maps job tone → visual style for consistent look across a video.
# Falls back to "cinematic" for any unrecognised tone.
_TONE_TO_STYLE: dict[str, str] = {
    "engaging":     "cinematic",
    "informative":  "documentary",
    "educational":  "documentary",
    "professional": "realistic",
    "formal":       "realistic",
    "inspiring":    "cinematic",
    "dramatic":     "cinematic",
    "casual":       "realistic",
    "fun":          "illustration",
    "creative":     "illustration",
    "minimalist":   "minimalist",
    "simple":       "minimalist",
}

VALID_VISUAL_STYLES = frozenset(
    {"cinematic", "realistic", "documentary", "minimalist", "illustration"}
)


def tone_to_visual_style(tone: str) -> str:
    """Map a job tone string to a visual style. Falls back to 'cinematic'."""
    if tone in VALID_VISUAL_STYLES:
        return tone
    return _TONE_TO_STYLE.get(tone.lower().strip(), "cinematic")


# ── Deterministic shot-type variation ────────────────────────────────────────

# Pattern cycles through shot types to create visual variety across scenes.
# Index is (scene_number % len(_SHOT_TYPES)) — fully deterministic.
_SHOT_TYPES = [
    "wide establishing shot",
    "medium shot",
    "close-up detail shot",
    "environmental context shot",
    "human action or reaction shot",
]


def get_shot_type(scene_number: int) -> str:
    """Return a deterministic shot type for the given 0-based scene number."""
    return _SHOT_TYPES[scene_number % len(_SHOT_TYPES)]


# ── Composition hints by aspect ratio ────────────────────────────────────────

_COMPOSITION_HINTS = {
    "16:9": (
        "wide cinematic framing, important subject in center third, "
        "negative space for caption overlay at bottom"
    ),
    "9:16": (
        "vertical portrait framing, subject centered vertically, "
        "safe central region, space for top and bottom caption overlays"
    ),
}

_DEFAULT_COMPOSITION_HINT = "balanced composition, subject clearly visible"


def get_composition_hint(aspect_ratio: str) -> str:
    """Return composition guidance string for the given aspect ratio."""
    return _COMPOSITION_HINTS.get(aspect_ratio, _DEFAULT_COMPOSITION_HINT)


# ── Prompt length control ─────────────────────────────────────────────────────

_DEFAULT_MAX_CHARS = 300


def get_max_prompt_chars() -> int:
    """Return the configured maximum visual prompt length."""
    try:
        return int(os.getenv("AI_VISUAL_PROMPT_MAX_CHARS", str(_DEFAULT_MAX_CHARS)))
    except (ValueError, TypeError):
        return _DEFAULT_MAX_CHARS


def truncate_prompt(prompt: str, max_chars: Optional[int] = None) -> str:
    """
    Truncate a visual prompt to max_chars, preserving whole words.

    Preserves the subject/action (start of the prompt) and trims the tail.
    """
    if max_chars is None:
        max_chars = get_max_prompt_chars()
    if len(prompt) <= max_chars:
        return prompt
    # Trim at last word boundary before max_chars
    truncated = prompt[:max_chars].rsplit(" ", 1)[0]
    return truncated.rstrip(",. ")


# ── Prompt templates ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a professional visual prompt engineer for AI image generation systems.
Your task is to convert scene narration into clear, effective visual prompts.

VISUAL PROMPT RULES:
1. Describe concrete visual subjects and environments
2. Match the narration's subject matter and tone
3. Be concise but descriptive (typically 20-60 words)
4. Describe lighting, composition, and atmosphere when relevant
5. Avoid text inside the image unless specifically required
6. Avoid logos, watermarks, and UI elements
7. Avoid copyrighted characters or real people unless specifically required
8. Avoid abstract concepts - focus on visible, tangible elements
9. Use natural language, not AI-specific keywords

ASPECT RATIO COMPOSITION:
- 16:9 (landscape): wide cinematic framing, subject in center third, leave bottom space for captions
- 9:16 (portrait): vertical framing, subject centered, safe central zone, space at top/bottom for captions

VISUAL STYLES:
- cinematic: dramatic lighting, film-like, professional aesthetic, rich colors
- realistic: photorealistic, natural lighting, documentary feel
- documentary: authentic, journalistic, natural environments
- minimalist: clean, simple, minimal elements, uncluttered
- illustration: artistic, stylized, illustrated aesthetic

Output ONLY the visual prompt text. No explanation, no quotes, no extra text.
"""

_SINGLE_SCENE_PROMPT_TEMPLATE = """\
Generate a visual prompt for this scene.

TOPIC: {topic}
SCENE: {scene_number} of {total_scenes}
SHOT TYPE: {shot_type}
ASPECT RATIO: {aspect_ratio}
VISUAL STYLE: {visual_style}
NARRATION: {narration}
VISUAL DESCRIPTION: {visual_description}
{prev_context}
COMPOSITION HINT: {composition_hint}

Generate a concise visual prompt (max 60 words) describing what should appear on screen.
The prompt must use the specified shot type and suit the scene content.
Do NOT repeat the narration — describe the visual elements only.
"""

_BATCH_PROMPT_TEMPLATE = """\
Generate visual prompts for a {total_scenes}-scene video.

TOPIC: {topic}
ASPECT RATIO: {aspect_ratio}
VISUAL STYLE: {visual_style}
COMPOSITION HINT: {composition_hint}

Create prompts that feel like they belong to the same video:
- Use the shot type specified per scene
- Vary the framing and focus across scenes
- Keep a consistent style throughout

SCENES:
{scenes_text}

Return ONLY a JSON array of strings, one prompt per scene, in the same order.
Each prompt: max 60 words, concrete visual description, no text in image.
"""


# ── Public API ────────────────────────────────────────────────────────────────

def generate_visual_prompt(
    narration: str,
    aspect_ratio: str = "16:9",
    visual_style: str = "cinematic",
    topic: str = "",
    visual_description: str = "",
    scene_number: int = 0,
    total_scenes: int = 1,
    prev_prompt: Optional[str] = None,
) -> Optional[str]:
    """
    Generate a visual prompt for a single scene.

    Phase 3F.6: now receives richer scene context for better quality.

    Args:
        narration:          Scene narration text
        aspect_ratio:       Target aspect ratio ("16:9" or "9:16")
        visual_style:       Visual style (cinematic, realistic, documentary, minimalist, illustration)
        topic:              Overall video topic for context
        visual_description: LLM-generated visual description from script
        scene_number:       0-based scene index (for shot type variation)
        total_scenes:       Total number of scenes in the video
        prev_prompt:        Previous scene's visual prompt for continuity context

    Returns:
        Generated visual prompt string, or None if generation fails.
    """
    try:
        shot_type = get_shot_type(scene_number)
        composition_hint = get_composition_hint(aspect_ratio)

        prev_context = ""
        if prev_prompt:
            prev_context = f"PREVIOUS SCENE: {prev_prompt[:100]}\n(Ensure this scene looks visually distinct from the previous.)"

        prompt = _SINGLE_SCENE_PROMPT_TEMPLATE.format(
            topic=topic or "general topic",
            scene_number=scene_number + 1,
            total_scenes=total_scenes,
            shot_type=shot_type,
            aspect_ratio=aspect_ratio,
            visual_style=visual_style,
            narration=narration,
            visual_description=visual_description or narration,
            prev_context=prev_context,
            composition_hint=composition_hint,
        )

        llm = get_llm_provider()
        result = llm.generate(
            prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.7,
            max_tokens=120,
        )

        result = result.strip().strip('"').strip("'")
        result = truncate_prompt(result)
        return result if result else None

    except (LLMError, LLMJSONError) as e:
        logger.warning("Visual prompt generation failed: %s. Continuing without prompt.", e)
        return None
    except Exception as e:
        logger.warning("Unexpected error in visual prompt generation: %s. Continuing without prompt.", e)
        return None


def generate_visual_prompts_batch(
    scenes: list[Scene],
    aspect_ratio: str = "16:9",
    visual_style: str = "cinematic",
    topic: str = "",
) -> list[Optional[str]]:
    """
    Generate visual prompts for all scenes in one LLM call.

    Phase 3F.6: includes topic, shot types, visual descriptions, composition hints.

    Args:
        scenes:       List of Scene objects
        aspect_ratio: Target aspect ratio
        visual_style: Visual style for the entire video
        topic:        Overall video topic

    Returns:
        List of prompts (one per scene), with None for failures.
    """
    if not scenes:
        return []

    total = len(scenes)
    composition_hint = get_composition_hint(aspect_ratio)
    max_chars = get_max_prompt_chars()

    try:
        # Build per-scene lines including shot type and visual description
        scene_lines = []
        for i, scene in enumerate(scenes):
            shot_type = get_shot_type(i)
            vd = (scene.visual_description or "").strip()
            narration = (scene.narration or "").strip()
            desc = vd if vd else narration
            scene_lines.append(
                f"[Scene {i+1}/{total}] Shot: {shot_type}\n"
                f"  Narration: {narration}\n"
                f"  Visual description: {desc}"
            )
        scenes_text = "\n\n".join(scene_lines)

        prompt = _BATCH_PROMPT_TEMPLATE.format(
            total_scenes=total,
            topic=topic or "general topic",
            aspect_ratio=aspect_ratio,
            visual_style=visual_style,
            composition_hint=composition_hint,
            scenes_text=scenes_text,
        )

        llm = get_llm_provider()
        logger.info(
            "Generating %d visual prompts in batch via %s (style=%s, topic=%r)",
            total, llm.provider_name, visual_style, topic[:50],
        )

        import json
        result = llm.generate(
            prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.7,
            max_tokens=2048,
        )

        try:
            prompts = json.loads(result)
            if isinstance(prompts, list):
                while len(prompts) < total:
                    prompts.append(None)
                # Apply truncation and clean-up
                prompts = [
                    truncate_prompt(str(p).strip().strip('"'), max_chars) if p else None
                    for p in prompts[:total]
                ]
                return prompts
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse visual prompts as JSON, falling back to individual generation"
            )

    except (LLMError, LLMJSONError) as e:
        logger.warning(
            "Batch visual prompt generation failed: %s. Falling back to individual generation.", e
        )
    except Exception as e:
        logger.warning(
            "Unexpected error in batch visual prompt generation: %s. Falling back to individual generation.", e
        )

    # Fallback: generate individually with continuity
    logger.info("Falling back to individual visual prompt generation")
    prompts: list[Optional[str]] = []
    for i, scene in enumerate(scenes):
        prev = prompts[i - 1] if i > 0 else None
        p = generate_visual_prompt(
            narration=scene.narration,
            aspect_ratio=aspect_ratio,
            visual_style=visual_style,
            topic=topic,
            visual_description=scene.visual_description or "",
            scene_number=i,
            total_scenes=total,
            prev_prompt=prev,
        )
        prompts.append(p)
    return prompts


def add_visual_prompts_to_scenes(
    scenes: list[Scene],
    aspect_ratio: str = "16:9",
    visual_style: str = "cinematic",
    use_batch: bool = True,
    topic: str = "",
) -> list[Scene]:
    """
    Add visual prompts to a list of Scene objects.

    Phase 3F.6: now accepts topic and passes richer context.

    Args:
        scenes:       List of Scene objects
        aspect_ratio: Target aspect ratio
        visual_style: Visual style for consistent look across the video
        use_batch:    Whether to use batch generation
        topic:        Overall video topic for visual context

    Returns:
        Updated list of Scene objects with visual_prompt populated.
    """
    if not scenes:
        return scenes

    logger.info(
        "Adding visual prompts to %d scenes (aspect_ratio=%s, style=%s, topic=%r)",
        len(scenes), aspect_ratio, visual_style, topic[:50],
    )

    if use_batch:
        prompts = generate_visual_prompts_batch(
            scenes, aspect_ratio=aspect_ratio, visual_style=visual_style, topic=topic
        )
    else:
        prompts = []
        for i, scene in enumerate(scenes):
            prev = prompts[i - 1] if i > 0 else None
            p = generate_visual_prompt(
                narration=scene.narration,
                aspect_ratio=aspect_ratio,
                visual_style=visual_style,
                topic=topic,
                visual_description=scene.visual_description or "",
                scene_number=i,
                total_scenes=len(scenes),
                prev_prompt=prev,
            )
            prompts.append(p)

    updated_scenes = []
    for scene, prompt in zip(scenes, prompts):
        scene_dict = scene.model_dump()
        scene_dict["visual_prompt"] = prompt
        updated_scenes.append(Scene(**scene_dict))

    success_count = sum(1 for p in prompts if p is not None)
    logger.info("Visual prompts added: %d/%d successful", success_count, len(scenes))

    return updated_scenes
