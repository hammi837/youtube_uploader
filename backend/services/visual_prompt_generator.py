"""
backend/services/visual_prompt_generator.py — Visual prompt generation service (Phase 3F.2).

Generates image/video generation prompts from scene narration.
Uses the existing LLM provider abstraction.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.content_models import Scene
from backend.services.llm.base import LLMError, LLMJSONError
from backend.services.llm.factory import get_llm_provider

logger = logging.getLogger(__name__)

# ── Prompt templates ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a professional visual prompt engineer for AI image and video generation systems.
Your task is to convert scene narration into clear, effective visual prompts.

VISUAL PROMPT RULES:
1. Describe concrete visual subjects and environments
2. Match the narration's subject matter and tone
3. Be concise but descriptive (typically 20-60 words)
4. Describe lighting, composition, and atmosphere when relevant
5. Avoid text inside the image unless specifically required
6. Avoid logos, watermarks, and UI elements
7. Avoid copyrighted characters or real people unless specifically required
8. Avoid abstract concepts - focus on visible elements
9. Use natural language, not AI-specific keywords

ASPECT RATIO GUIDELINES:
- For 16:9 (landscape): Allow wider environmental compositions, avoid placing primary subject too close to extreme edges
- For 9:16 (portrait): Prefer vertically composed subjects, keep important subject near center, avoid wide compositions

STYLE AWARENESS:
Adapt prompts to match the requested visual style:
- cinematic: dramatic lighting, film-like composition, professional aesthetic
- realistic: photorealistic, natural lighting, documentary style
- documentary: authentic, journalistic style, natural environments
- minimalist: clean, simple compositions, minimal elements
- illustration: artistic, stylized, illustrated aesthetic

Output ONLY the visual prompt text. No explanation, no quotes, no extra text.
"""

_SINGLE_SCENE_PROMPT_TEMPLATE = """\
Generate a visual prompt for this scene narration.

NARRATION: {narration}
ASPECT RATIO: {aspect_ratio}
VISUAL STYLE: {visual_style}

Generate a concise visual prompt describing what should appear on screen.
"""

_BATCH_PROMPT_TEMPLATE = """\
Generate visual prompts for these scenes.

ASPECT RATIO: {aspect_ratio}
VISUAL STYLE: {visual_style}

For each scene, generate a concise visual prompt describing what should appear on screen.

SCENES:
{scenes_text}

Return the prompts as a JSON array of strings in the same order as the scenes.
"""


# ── Public API ────────────────────────────────────────────────────────────────

def generate_visual_prompt(
    narration: str,
    aspect_ratio: str = "16:9",
    visual_style: str = "cinematic",
) -> Optional[str]:
    """
    Generate a visual prompt for a single scene narration.

    Args:
        narration: Scene narration text
        aspect_ratio: Target aspect ratio (16:9 or 9:16)
        visual_style: Visual style (cinematic, realistic, documentary, minimalist, illustration)

    Returns:
        Generated visual prompt, or None if generation fails.

    Failure handling:
        - Returns None instead of raising exceptions
        - Allows video generation to continue without visual prompts
    """
    try:
        prompt = _SINGLE_SCENE_PROMPT_TEMPLATE.format(
            narration=narration,
            aspect_ratio=aspect_ratio,
            visual_style=visual_style,
        )

        llm = get_llm_provider()
        logger.info("Generating visual prompt for scene via %s", llm.provider_name)

        result = llm.generate(
            prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.7,
            max_tokens=100,
        )

        # Clean up the result (remove quotes if present)
        result = result.strip().strip('"').strip("'")
        logger.debug("Generated visual prompt: %s", result)

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
) -> list[Optional[str]]:
    """
    Generate visual prompts for multiple scenes in a single LLM call.

    Args:
        scenes: List of Scene objects
        aspect_ratio: Target aspect ratio (16:9 or 9:16)
        visual_style: Visual style (cinematic, realistic, documentary, minimalist, illustration)

    Returns:
        List of visual prompts (one per scene), with None for failed generations.

    Failure handling:
        - Returns None for failed individual prompts
        - Allows video generation to continue with partial prompts
    """
    if not scenes:
        return []

    try:
        # Build scenes text for the prompt
        scenes_text = "\n".join(
            f"[{i+1}] {scene.narration}"
            for i, scene in enumerate(scenes)
        )

        prompt = _BATCH_PROMPT_TEMPLATE.format(
            aspect_ratio=aspect_ratio,
            visual_style=visual_style,
            scenes_text=scenes_text,
        )

        llm = get_llm_provider()
        logger.info(
            "Generating %d visual prompts in batch via %s",
            len(scenes),
            llm.provider_name,
        )

        import json
        result = llm.generate(
            prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.7,
            max_tokens=2048,
        )

        # Parse JSON array
        try:
            prompts = json.loads(result)
            if isinstance(prompts, list):
                # Ensure we have one prompt per scene
                while len(prompts) < len(scenes):
                    prompts.append(None)
                return prompts[:len(scenes)]
        except json.JSONDecodeError:
            logger.warning("Failed to parse visual prompts as JSON, falling back to individual generation")

    except (LLMError, LLMJSONError) as e:
        logger.warning("Batch visual prompt generation failed: %s. Falling back to individual generation.", e)
    except Exception as e:
        logger.warning("Unexpected error in batch visual prompt generation: %s. Falling back to individual generation.", e)

    # Fallback: generate individually
    logger.info("Falling back to individual visual prompt generation")
    return [
        generate_visual_prompt(scene.narration, aspect_ratio, visual_style)
        for scene in scenes
    ]


def add_visual_prompts_to_scenes(
    scenes: list[Scene],
    aspect_ratio: str = "16:9",
    visual_style: str = "cinematic",
    use_batch: bool = True,
) -> list[Scene]:
    """
    Add visual prompts to a list of Scene objects.

    Args:
        scenes: List of Scene objects
        aspect_ratio: Target aspect ratio (16:9 or 9:16)
        visual_style: Visual style (cinematic, realistic, documentary, minimalist, illustration)
        use_batch: Whether to use batch generation (faster) or individual generation

    Returns:
        Updated list of Scene objects with visual_prompt field populated.

    Failure handling:
        - Scenes with failed prompt generation keep visual_prompt as None
        - Does not fail the entire operation for individual failures
    """
    if not scenes:
        return scenes

    logger.info(
        "Adding visual prompts to %d scenes (aspect_ratio=%s, style=%s)",
        len(scenes),
        aspect_ratio,
        visual_style,
    )

    if use_batch:
        prompts = generate_visual_prompts_batch(scenes, aspect_ratio, visual_style)
    else:
        prompts = [
            generate_visual_prompt(scene.narration, aspect_ratio, visual_style)
            for scene in scenes
        ]

    # Update scenes with generated prompts
    updated_scenes = []
    for scene, prompt in zip(scenes, prompts):
        # Create a new Scene object with the visual_prompt added
        scene_dict = scene.model_dump()
        scene_dict["visual_prompt"] = prompt
        updated_scenes.append(Scene(**scene_dict))

    success_count = sum(1 for p in prompts if p is not None)
    logger.info(
        "Visual prompts added: %d/%d successful",
        success_count,
        len(scenes),
    )

    return updated_scenes
