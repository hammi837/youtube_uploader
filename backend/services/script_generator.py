"""
backend/services/script_generator.py — Script generation service.

Orchestrates research → LLM prompt → structured JSON → validation.

P0.3: Target narration length is derived from requested duration × WPM.
The LLM's estimated_duration_seconds is NOT trusted as ground truth.
"""

from __future__ import annotations

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
from backend.services.script_length import (
    ScriptTooShortError,
    count_script_narration_words,
    count_words,
    estimate_duration_from_words,
    get_narration_wpm,
    min_word_count,
    target_word_count,
    words_per_scene,
)

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
- Do NOT pad with repeated sentences, empty platitudes, or meaningless filler.
"""

_SCRIPT_PROMPT_TEMPLATE = """\
Generate a complete YouTube video script for the following topic.

TOPIC: {topic}
LANGUAGE: {language}
TONE: {tone}
TARGET DURATION: {target_duration_seconds} seconds \
({target_duration_minutes:.1f} minutes)
NUMBER OF SCENES: {scene_count}
TARGET SPOKEN WORD COUNT: {target_words} words total \
(planning rate ≈ {wpm:.0f} words/minute)
MINIMUM ACCEPTABLE WORDS: {min_words}
APPROXIMATE WORDS PER SCENE (excluding hook): {words_per_scene}

--- RESEARCH CONTEXT ---
{research_context}
--- END RESEARCH CONTEXT ---

Generate a structured JSON object with EXACTLY this schema:

{{
  "title": "YouTube video title (max 100 chars, engaging but not misleading)",
  "description": "YouTube description (2–4 paragraphs, naturally includes relevant keywords, \
no keyword stuffing, no fabricated references)",
  "tags": ["tag1", "tag2", ...],
  "hook": "Opening narration only (about 35–50 words). Do NOT repeat this text inside scenes.",
  "estimated_duration_seconds": <integer>,
  "scenes": [
    {{
      "scene_number": 1,
      "narration": "Spoken narration for this scene — enough words to meet the per-scene budget",
      "visual_description": "Internal visual guidance for image generation (NOT spoken aloud)",
      "estimated_duration_seconds": <integer>
    }},
    ...
  ]
}}

LENGTH REQUIREMENTS (critical):
- The TOTAL spoken words (hook + all scene narrations) MUST be at least {min_words} \
and should be close to {target_words}.
- Write substantial scene narration (typically several sentences per scene), \
not 1–2 short lines.
- Cover the topic thoroughly using the research: explanations, examples, \
comparisons, and a clear closing takeaway.
- Do NOT meet the word count by repeating the same sentence or padding with fluff.
- estimated_duration_seconds fields are hints only; word count is what matters.

OTHER RULES:
- Title: max 100 characters. Natural and clickable, not misleading.
- Tags: 5–15 relevant tags. No spam. Array of strings.
- Visual descriptions: concise, actionable; never spoken.
- The script must be suitable for faceless narrated YouTube content.
- ONLY output valid JSON. No markdown, no explanation, no text outside the JSON object.
"""

_EXPAND_PROMPT_TEMPLATE = """\
The following YouTube script is TOO SHORT for the target runtime.

TOPIC: {topic}
TARGET DURATION: {target_duration_seconds} seconds
TARGET SPOKEN WORDS: {target_words}
CURRENT SPOKEN WORDS: {current_words}
SHORTFALL: about {shortfall} words
RESEARCH CONTEXT (factual basis only):
{research_context}

CURRENT SCRIPT JSON:
{script_json}

Return an UPDATED JSON object with the SAME schema (title, description, tags, hook, \
estimated_duration_seconds, scenes). Expand scene narrations (and lightly the hook if needed) \
so total spoken words reach at least {min_words} and ideally {target_words}.

Rules:
- Keep the same topic, tone, and approximate scene count.
- Use ONLY facts supported by the research context — do not invent studies or numbers.
- Do NOT repeat sentences to pad length.
- Do NOT put visual prompts into narration.
- ONLY output valid JSON.
"""


# ── Public API ────────────────────────────────────────────────────────────────

def generate_script(request: ScriptRequest) -> tuple[ResearchResult, GeneratedScript]:
    """
    Research a topic and generate a structured script with duration-aware length.

    Raises:
        ResearchError / ResearchNoSourcesError
        LLMError / LLMJSONError
        ScriptTooShortError: narration still too short after one expansion
    """
    wpm = get_narration_wpm()
    target_words = target_word_count(request.target_duration_seconds, wpm=wpm)
    min_words = min_word_count(request.target_duration_seconds, wpm=wpm)
    per_scene = words_per_scene(target_words, request.scene_count)

    logger.info(
        "Script generation started: topic='%s' duration=%ds scenes=%d "
        "target_words=%d min_words=%d wpm=%.0f",
        request.topic, request.target_duration_seconds, request.scene_count,
        target_words, min_words, wpm,
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

    if not research.sources:
        raise ResearchNoSourcesError(
            f"No reliable web sources were found for: '{request.topic}'. "
            "Script generation requires research-backed content. "
            "Try rephrasing the topic or using different keywords."
        )

    research_context = _build_research_context(research)
    prompt = _SCRIPT_PROMPT_TEMPLATE.format(
        topic=request.topic,
        language=request.language,
        tone=request.tone,
        target_duration_seconds=request.target_duration_seconds,
        target_duration_minutes=request.target_duration_seconds / 60,
        scene_count=request.scene_count,
        target_words=target_words,
        min_words=min_words,
        words_per_scene=per_scene,
        wpm=wpm,
        research_context=research_context,
    )

    llm = get_llm_provider()
    logger.info("Using LLM provider: %s", llm.provider_name)

    raw_json = llm.generate_json(
        prompt,
        system_prompt=_SYSTEM_PROMPT,
        temperature=0.6,
        max_tokens=8192,
    )
    script = _validate_script(raw_json, request)
    word_count = count_script_narration_words(script)
    logger.info(
        "Initial script word count=%d (target=%d min=%d)",
        word_count, target_words, min_words,
    )

    # ── One controlled expansion if materially short ───────────────────────
    if word_count < min_words:
        logger.warning(
            "Script too short (%d < %d). Attempting one expansion pass.",
            word_count, min_words,
        )
        script = _expand_script_once(
            llm=llm,
            request=request,
            research_context=research_context,
            script=script,
            current_words=word_count,
            target_words=target_words,
            min_words=min_words,
        )
        word_count = count_script_narration_words(script)
        logger.info(
            "After expansion word count=%d (target=%d min=%d)",
            word_count, target_words, min_words,
        )

    if word_count < min_words:
        raise ScriptTooShortError(
            f"Script narration is too short for a {request.target_duration_seconds}s video: "
            f"{word_count} words (minimum {min_words}, target {target_words} at {wpm:.0f} WPM). "
            "Regeneration did not reach the required length."
        )

    # Duration estimate from actual words — never trust LLM duration claims
    script = _apply_word_based_timing(script, request, word_count, wpm)

    logger.info(
        "Script generation completed: title='%s' scenes=%d words=%d "
        "estimated_duration=%ds (from words, not LLM claim)",
        script.title, len(script.scenes), word_count, script.estimated_duration_seconds,
    )
    return research, script


# ── Internal helpers ──────────────────────────────────────────────────────────

def _expand_script_once(
    *,
    llm,
    request: ScriptRequest,
    research_context: str,
    script: GeneratedScript,
    current_words: int,
    target_words: int,
    min_words: int,
) -> GeneratedScript:
    import json as _json

    prompt = _EXPAND_PROMPT_TEMPLATE.format(
        topic=request.topic,
        target_duration_seconds=request.target_duration_seconds,
        target_words=target_words,
        min_words=min_words,
        current_words=current_words,
        shortfall=max(0, target_words - current_words),
        research_context=research_context,
        script_json=_json.dumps(script.model_dump(), ensure_ascii=False),
    )
    try:
        raw = llm.generate_json(
            prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.5,
            max_tokens=8192,
        )
        expanded = _validate_script(raw, request)
        if count_script_narration_words(expanded) >= current_words:
            return expanded
        logger.warning("Expansion returned fewer/equal words — keeping previous script.")
        return script
    except (LLMError, LLMJSONError) as exc:
        logger.warning("Script expansion failed (%s) — keeping previous script.", exc)
        return script


def _apply_word_based_timing(
    script: GeneratedScript,
    request: ScriptRequest,
    word_count: int,
    wpm: float,
) -> GeneratedScript:
    """Overwrite estimated durations from word counts (proportional per scene)."""
    hook_words = count_words(script.hook or "")
    scene_word_counts = [count_words(s.narration) for s in script.scenes]
    total_parts = hook_words + sum(scene_word_counts)
    est_total = estimate_duration_from_words(word_count, wpm=wpm)

    # Prefer target duration when word count is in band; else use word-implied length
    target = int(request.target_duration_seconds)
    if abs(est_total - target) / max(target, 1) <= 0.20:
        timing_total = target
    else:
        timing_total = est_total

    updated_scenes: list[Scene] = []
    for i, scene in enumerate(script.scenes):
        sw = scene_word_counts[i] if i < len(scene_word_counts) else 0
        if total_parts > 0:
            share = sw / total_parts
        else:
            share = 1.0 / max(len(script.scenes), 1)
        scene_secs = max(5, int(round(timing_total * share)))
        updated_scenes.append(Scene(
            scene_number=scene.scene_number,
            narration=scene.narration,
            visual_description=scene.visual_description,
            estimated_duration_seconds=scene_secs,
        ))

    return GeneratedScript(
        title=script.title,
        description=script.description,
        tags=script.tags,
        hook=script.hook,
        estimated_duration_seconds=timing_total,
        scenes=updated_scenes,
    )


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
        lines.append("\nKey facts extracted from research:")
        for fact in research.key_facts[:10]:
            lines.append(f"  - {fact}")

    return "\n".join(lines)


def _validate_script(raw: dict[str, Any], request: ScriptRequest) -> GeneratedScript:
    """
    Validate and coerce the LLM JSON output into a GeneratedScript.

    Applies sensible defaults and clamping for fields that are present
    but out of range, rather than rejecting valid partial responses.
    """
    title = str(raw.get("title", request.topic))[:100]

    raw_tags = raw.get("tags", [])
    if isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags if str(t).strip()][:20]
    else:
        tags = []

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
            continue
        scenes.append(Scene(
            scene_number=int(s.get("scene_number", i + 1)),
            narration=narration,
            visual_description=str(s.get("visual_description", "")).strip(),
            estimated_duration_seconds=max(5, int(s.get("estimated_duration_seconds", 15))),
        ))

    if not scenes:
        raise LLMJSONError("No valid scenes found in LLM response after filtering.")

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
