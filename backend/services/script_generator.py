"""
backend/services/script_generator.py — Script generation service.

Orchestrates research → LLM prompt → structured JSON → validation.

Factuality rules (enforced in prompts):
  - The LLM must use supplied research as the factual basis.
  - It must NOT invent statistics, dates, quotations, or studies.
  - It must NOT fabricate sources or URLs.
  - Uncertain claims must be phrased cautiously or omitted.

The generated script is validated against the GeneratedScript Pydantic model
before being returned or persisted.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.content_models import (
    GeneratedScript,
    Scene,
    ScriptRequest,
)
from backend.services.llm.base import LLMError, LLMJSONError
from backend.services.llm.factory import get_llm_provider
from backend.services.research.base import ResearchNoSourcesError, ResearchResult
from backend.services.research.factory import get_research_provider

logger = logging.getLogger(__name__)

# ── Prompt templates ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a professional YouTube scriptwriter specializing in engaging, informative, \
faceless narrated videos. You write clear, natural-sounding narration that works \
well when converted to text-to-speech audio.

FACTUALITY RULES — follow strictly:
1. Use ONLY the research context provided as your factual basis.
2. Do NOT invent statistics, dates, quotations, studies, or specific claims \
   not present in the research.
3. Do NOT fabricate sources, URLs, or citations.
4. If you are uncertain about a fact, phrase it cautiously ("it is reported that…", \
   "according to some sources…") or omit it entirely.
5. Do NOT make claims that directly contradict the research.

WRITING STYLE:
- Natural, conversational narration — suitable for TTS audio.
- No filler phrases ("In this video we will…", "Don't forget to like and subscribe").
- Strong hook in the first 10–15 seconds.
- Clear transitions between scenes.
- Engaging but not clickbait. No fake claims.
- Target audience: curious general public.
"""

_SCRIPT_PROMPT_TEMPLATE = """\
Generate a complete YouTube video script for the following topic.

TOPIC: {topic}
LANGUAGE: {language}
TONE: {tone}
TARGET DURATION: approximately {target_duration_seconds} seconds \
({target_duration_minutes:.1f} minutes)
NUMBER OF SCENES: approximately {scene_count}

--- RESEARCH CONTEXT ---
{research_context}
--- END RESEARCH CONTEXT ---

Generate a structured JSON object with EXACTLY this schema:

{{
  "title": "YouTube video title (max 100 chars, engaging but not misleading)",
  "description": "YouTube description (2–4 paragraphs, naturally includes relevant keywords, \
no keyword stuffing, no fabricated references)",
  "tags": ["tag1", "tag2", ...],
  "hook": "First 10–20 seconds of narration — must immediately grab attention",
  "estimated_duration_seconds": <integer>,
  "scenes": [
    {{
      "scene_number": 1,
      "narration": "The spoken narration text for this scene",
      "visual_description": "Description for a future visual-generation system. \
Describe what should be shown on screen (B-roll, graphics, text overlay, etc.)",
      "estimated_duration_seconds": <integer>
    }},
    ...
  ]
}}

IMPORTANT:
- Title: max 100 characters. Natural and clickable, not misleading.
- Tags: 5–15 relevant tags. No spam. Array of strings.
- Hook narration: 2–4 sentences maximum.
- Each scene narration: 1–3 sentences.
- Visual descriptions: concise, actionable (e.g. "Aerial view of a city at night", \
"Text overlay: 3 FACTS", "Close-up of a computer screen showing code").
- Total scene estimated_duration_seconds must roughly sum to target duration.
- The script must be suitable for faceless narrated YouTube content.
- ONLY output valid JSON. No markdown, no explanation, no text outside the JSON object.
"""


# ── Public API ────────────────────────────────────────────────────────────────

def generate_script(request: ScriptRequest) -> tuple[ResearchResult, GeneratedScript]:
    """
    Research a topic and generate a structured script.

    Steps:
        1. Research the topic via the configured research provider.
        2. Build a research-grounded prompt.
        3. Call the LLM to generate structured JSON.
        4. Validate the JSON against GeneratedScript Pydantic model.
        5. Return (ResearchResult, GeneratedScript).

    Raises:
        ResearchError:    Research step failed.
        LLMError:         LLM step failed.
        LLMJSONError:     LLM returned malformed JSON.
        ValidationError:  Pydantic validation failed.
    """
    logger.info(
        "Script generation started: topic='%s' duration=%ds scenes=%d",
        request.topic, request.target_duration_seconds, request.scene_count,
    )

    # ── Step 1: Research ───────────────────────────────────────────────────
    logger.info("Research started for topic: '%s'", request.topic)
    research_provider = get_research_provider()
    research = research_provider.research(
        topic=request.topic,
        language=request.language,
        max_sources=6,
        depth="standard",
    )
    logger.info(
        "Research completed: %d sources, %d key facts",
        len(research.sources), len(research.key_facts),
    )

    # ── Step 1b: Guard — never generate with zero sources ─────────────────
    if not research.sources:
        raise ResearchNoSourcesError(
            f"No reliable web sources were found for: '{request.topic}'. "
            "Script generation requires research-backed content. "
            "Try rephrasing the topic or using different keywords."
        )

    # ── Step 2: Build prompt ───────────────────────────────────────────────
    research_context = _build_research_context(research)
    prompt = _SCRIPT_PROMPT_TEMPLATE.format(
        topic=request.topic,
        language=request.language,
        tone=request.tone,
        target_duration_seconds=request.target_duration_seconds,
        target_duration_minutes=request.target_duration_seconds / 60,
        scene_count=request.scene_count,
        research_context=research_context,
    )

    # ── Step 3: LLM generation ─────────────────────────────────────────────
    logger.info("Script generation started via LLM provider")
    llm = get_llm_provider()
    logger.info("Using LLM provider: %s", llm.provider_name)

    raw_json = llm.generate_json(
        prompt,
        system_prompt=_SYSTEM_PROMPT,
        temperature=0.6,
        max_tokens=4096,
    )

    logger.info("LLM response received, validating schema")

    # ── Step 4: Validate and coerce ────────────────────────────────────────
    script = _validate_script(raw_json, request)

    logger.info(
        "Script generation completed: title='%s' scenes=%d duration=%ds",
        script.title, len(script.scenes), script.estimated_duration_seconds,
    )

    return research, script


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_research_context(research: ResearchResult) -> str:
    """Format research results into a concise text block for the LLM prompt."""
    lines: list[str] = [f"Topic: {research.topic}"]

    if research.sources:
        lines.append(f"\nSources ({len(research.sources)}):")
        for i, src in enumerate(research.sources, 1):
            lines.append(f"\n[{i}] {src.title}")
            if src.url:
                lines.append(f"    URL: {src.url}")
            if src.snippet:
                lines.append(f"    Summary: {src.snippet[:300]}")
            if src.key_points:
                for point in src.key_points[:3]:
                    lines.append(f"    • {point}")

    if research.key_facts:
        lines.append(f"\nKey facts extracted from research:")
        for fact in research.key_facts[:10]:
            lines.append(f"  - {fact}")

    return "\n".join(lines)


def _validate_script(raw: dict[str, Any], request: ScriptRequest) -> GeneratedScript:
    """
    Validate and coerce the LLM JSON output into a GeneratedScript.

    Applies sensible defaults and clamping for fields that are present
    but out of range, rather than rejecting valid partial responses.
    """
    # Coerce title to max 100 chars
    title = str(raw.get("title", request.topic))[:100]

    # Ensure tags is a list of strings
    raw_tags = raw.get("tags", [])
    if isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags if str(t).strip()][:20]
    else:
        tags = []

    # Ensure scenes is a list
    raw_scenes = raw.get("scenes", [])
    if not isinstance(raw_scenes, list) or len(raw_scenes) == 0:
        raise LLMJSONError(
            "LLM response missing 'scenes' array or scenes is empty. "
            "This is a required field. Retry may succeed."
        )

    scenes: list[Scene] = []
    for i, s in enumerate(raw_scenes):
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration", "")).strip()
        if not narration:
            continue  # skip empty scenes
        scenes.append(Scene(
            scene_number=int(s.get("scene_number", i + 1)),
            narration=narration,
            visual_description=str(s.get("visual_description", "")).strip(),
            estimated_duration_seconds=max(5, int(s.get("estimated_duration_seconds", 15))),
        ))

    if not scenes:
        raise LLMJSONError("No valid scenes found in LLM response after filtering.")

    # Compute or use estimated duration
    scene_total = sum(sc.estimated_duration_seconds for sc in scenes)
    estimated_duration = int(raw.get("estimated_duration_seconds", scene_total))
    if estimated_duration <= 0:
        estimated_duration = scene_total

    return GeneratedScript(
        title=title,
        description=str(raw.get("description", "")).strip(),
        tags=tags,
        hook=str(raw.get("hook", "")).strip(),
        estimated_duration_seconds=estimated_duration,
        scenes=scenes,
    )
