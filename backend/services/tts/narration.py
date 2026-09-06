"""
backend/services/tts/narration.py — Narration text extractor.

Converts a GeneratedScript (or its DB record) into clean spoken text
suitable for TTS synthesis.

Design principles:
  - Extract ONLY fields that should be spoken aloud.
  - Preserve natural punctuation (periods, commas, question marks)
    because these improve TTS rhythm and intonation.
  - Do NOT include: metadata, URLs, tags, database IDs, JSON field names,
    visual descriptions, or instructions to the AI.
  - The output is a single string ready to pass to a TTS provider.

Structure of the narrated script:
    Hook  (opening attention-grabber)
    Scene 1 narration
    Scene 2 narration
    ...
    Scene N narration

Note: The YouTube description is NOT included — it is metadata, not narration.
The tags, title, and visual_description fields are NOT spoken.
"""

from __future__ import annotations

import re
from typing import Any


# ── Public API ────────────────────────────────────────────────────────────────

def extract_narration_text(script: Any) -> str:
    """
    Extract clean narration text from a GeneratedScript Pydantic model
    or a GeneratedScriptRecord ORM object.

    Args:
        script: Either a GeneratedScript Pydantic model (with .hook and .scenes)
                or a GeneratedScriptRecord ORM object (with .hook and .scenes_json).

    Returns:
        A single string of narration text suitable for TTS.

    Raises:
        ValueError: If no narration text can be extracted.
    """
    hook   = _get_hook(script)
    scenes = _get_scenes(script)

    parts: list[str] = []

    # Hook — spoken first as the opening attention-grabber
    if hook and hook.strip():
        parts.append(_normalize(hook))

    # Scene narrations — spoken in order
    for scene in scenes:
        narration = _get_scene_narration(scene)
        if narration:
            parts.append(_normalize(narration))

    if not parts:
        raise ValueError(
            "No narration text found in the script. "
            "The script must have a hook or at least one scene with narration."
        )

    return _join_parts(parts)


def estimate_narration_duration(text: str) -> float:
    """
    Estimate spoken duration from word count.

    Average spoken English rate: ~150 words/minute.
    Returns duration in seconds.
    """
    words = len(text.split())
    return round(words / 150 * 60, 1)


def count_narration_words(text: str) -> int:
    """Return the word count of narration text."""
    return len(text.split())


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_hook(script: Any) -> str:
    """Extract hook text from either a Pydantic model or ORM record."""
    # Pydantic model (GeneratedScript)
    if hasattr(script, "hook"):
        return str(script.hook or "")
    return ""


def _get_scenes(script: Any) -> list[Any]:
    """
    Extract scenes list from either a Pydantic model or ORM record.

    For Pydantic: script.scenes → list[Scene]
    For ORM: script.scenes_as_list() → list[dict]
    """
    # Pydantic model (GeneratedScript) — has .scenes attribute
    if hasattr(script, "scenes") and isinstance(script.scenes, list):
        return script.scenes

    # ORM record (GeneratedScriptRecord) — has scenes_json / scenes_as_list()
    if hasattr(script, "scenes_as_list"):
        return script.scenes_as_list()

    return []


def _get_scene_narration(scene: Any) -> str:
    """
    Extract the narration field from a scene.

    Handles both Pydantic Scene objects (scene.narration)
    and raw dicts (scene["narration"]).
    """
    if hasattr(scene, "narration"):
        return str(scene.narration or "")
    if isinstance(scene, dict):
        return str(scene.get("narration", "") or "")
    return ""


def _normalize(text: str) -> str:
    """
    Normalize a piece of narration text.

    - Strip leading/trailing whitespace.
    - Remove multiple blank lines.
    - Remove markdown artifacts.
    - Preserve natural punctuation.
    """
    # Remove markdown bold/italic
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.+?)_{1,3}", r"\1", text)
    # Remove markdown headers (should not appear in narration but be safe)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    # Remove inline code
    text = re.sub(r"`[^`]+`", "", text)
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    # Remove trailing spaces on lines
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


def _join_parts(parts: list[str]) -> str:
    """
    Join narration parts with double newlines (paragraph breaks).

    This gives Edge-TTS natural pause cues between scenes.
    """
    return "\n\n".join(p for p in parts if p.strip())
