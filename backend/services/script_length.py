"""
backend/services/script_length.py — P0.3 narration length helpers.

Target spoken length is derived from requested duration × words-per-minute,
NOT from the LLM's claimed estimated_duration_seconds.
"""

from __future__ import annotations

import os
import re
from typing import Any


# Default spoken rate for planning (within the 135–145 WPM band).
_DEFAULT_WPM = 140.0
# Accept scripts at ≥85% of the target word count after generation/expansion.
_DEFAULT_MIN_RATIO = 0.85


class ScriptTooShortError(ValueError):
    """Raised when narration word count is materially below the duration target."""


def get_narration_wpm() -> float:
    """Configurable narration planning rate (words per minute)."""
    try:
        wpm = float(os.getenv("NARRATION_WPM", str(_DEFAULT_WPM)))
    except (TypeError, ValueError):
        wpm = _DEFAULT_WPM
    return max(100.0, min(180.0, wpm))


def get_script_min_word_ratio() -> float:
    try:
        ratio = float(os.getenv("SCRIPT_LENGTH_MIN_RATIO", str(_DEFAULT_MIN_RATIO)))
    except (TypeError, ValueError):
        ratio = _DEFAULT_MIN_RATIO
    return max(0.5, min(1.0, ratio))


def target_word_count(duration_seconds: int | float, wpm: float | None = None) -> int:
    """Words needed to fill duration_seconds at the planning WPM."""
    rate = get_narration_wpm() if wpm is None else wpm
    seconds = max(1.0, float(duration_seconds))
    return max(1, int(round(seconds * rate / 60.0)))


def min_word_count(duration_seconds: int | float, wpm: float | None = None) -> int:
    """Soft floor — below this after one expansion, generation fails clearly."""
    target = target_word_count(duration_seconds, wpm=wpm)
    return max(1, int(round(target * get_script_min_word_ratio())))


def count_words(text: str) -> int:
    if not text or not str(text).strip():
        return 0
    return len(re.findall(r"\b[\w']+\b", str(text), flags=re.UNICODE))


def count_script_narration_words(script: Any) -> int:
    """
    Count spoken words the same way TTS will hear them:
    hook + each scene narration (visual_description excluded).
    """
    from backend.services.tts.narration import extract_narration_text

    try:
        text = extract_narration_text(script)
    except ValueError:
        return 0
    return count_words(text)


def estimate_duration_from_words(word_count: int, wpm: float | None = None) -> int:
    """Seconds implied by word_count at planning WPM."""
    rate = get_narration_wpm() if wpm is None else wpm
    if rate <= 0:
        rate = _DEFAULT_WPM
    return max(1, int(round(word_count / rate * 60.0)))


def words_per_scene(total_words: int, scene_count: int, hook_words: int = 40) -> int:
    """Rough per-scene narration budget after reserving hook words."""
    scenes = max(1, int(scene_count))
    remaining = max(total_words - max(0, hook_words), scenes)
    return max(1, int(round(remaining / scenes)))
