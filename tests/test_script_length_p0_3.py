"""P0.3 — script length targeting and scene timing vs real audio."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from backend.content_models import GeneratedScript, Scene, ScriptRequest
from backend.services.script_length import (
    ScriptTooShortError,
    count_script_narration_words,
    count_words,
    estimate_duration_from_words,
    min_word_count,
    target_word_count,
)


def test_target_word_count_180s_at_140_wpm(monkeypatch):
    monkeypatch.setenv("NARRATION_WPM", "140")
    assert target_word_count(180) == 420
    assert min_word_count(180) == int(round(420 * 0.85))


def test_count_script_includes_hook_and_scenes_not_visuals():
    script = GeneratedScript(
        title="T",
        description="d",
        tags=["a"],
        hook="One two three four five.",
        estimated_duration_seconds=60,
        scenes=[
            Scene(
                scene_number=1,
                narration="Six seven eight nine ten.",
                visual_description="THIS SHOULD NOT COUNT eleven twelve",
                estimated_duration_seconds=30,
            )
        ],
    )
    assert count_script_narration_words(script) == 10


def test_calculate_scene_durations_ignores_inflated_estimates():
    from backend.services.video.ffmpeg_assembler import calculate_scene_durations

    scenes = [
        {"narration": "a" * 50, "estimated_duration_seconds": 60},
        {"narration": "b" * 50, "estimated_duration_seconds": 60},
    ]
    # Audio is only 60s — must not keep 60+60 fake estimates
    durations = calculate_scene_durations(scenes, 60.0)
    assert abs(sum(durations) - 60.0) < 1.0
    assert all(d >= 1.0 for d in durations)


def test_calculate_scene_durations_proportional_to_narration():
    from backend.services.video.ffmpeg_assembler import calculate_scene_durations

    scenes = [
        {"narration": "x" * 100},
        {"narration": "x" * 200},
        {"narration": "x" * 100},
    ]
    durations = calculate_scene_durations(scenes, 120.0)
    assert abs(sum(durations) - 120.0) < 1.0
    assert durations[1] > durations[0]


def test_validate_script_still_works_short_mock():
    from backend.services.script_generator import _validate_script

    request = ScriptRequest(topic="test", scene_count=3, target_duration_seconds=60)
    raw = {
        "title": "Hello",
        "description": "d",
        "tags": ["t"],
        "hook": "Hook text here.",
        "estimated_duration_seconds": 60,
        "scenes": [
            {
                "scene_number": 1,
                "narration": "Narration one.",
                "visual_description": "v",
                "estimated_duration_seconds": 20,
            }
        ],
    }
    script = _validate_script(raw, request)
    assert script.title == "Hello"
    assert len(script.scenes) == 1


def test_generate_script_expands_then_raises_if_still_short(monkeypatch):
    monkeypatch.setenv("NARRATION_WPM", "140")
    monkeypatch.setenv("SCRIPT_LENGTH_MIN_RATIO", "0.85")

    from backend.services import script_generator as sg
    from backend.services.research.base import ResearchResult, ResearchSource

    short_json = {
        "title": "Short",
        "description": "d",
        "tags": ["t"],
        "hook": "Tiny hook.",
        "estimated_duration_seconds": 180,
        "scenes": [
            {
                "scene_number": i,
                "narration": "Only a few words here.",
                "visual_description": "v",
                "estimated_duration_seconds": 18,
            }
            for i in range(1, 13)
        ],
    }

    research = ResearchResult(
        topic="magnets",
        sources=[
            ResearchSource(
                title="Magnets",
                url="https://example.com",
                snippet="Magnets attract.",
                key_points=["attraction"],
            )
        ],
        key_facts=["Magnets attract iron."],
        research_summary="summary",
    )

    mock_provider = MagicMock()
    mock_provider.research.return_value = research
    mock_llm = MagicMock()
    mock_llm.provider_name = "mock"
    mock_llm.generate_json.return_value = short_json

    with patch.object(sg, "get_research_provider", return_value=mock_provider), \
         patch.object(sg, "get_llm_provider", return_value=mock_llm):
        with pytest.raises(ScriptTooShortError):
            sg.generate_script(
                ScriptRequest(
                    topic="How magnets work",
                    target_duration_seconds=180,
                    scene_count=12,
                )
            )
    # Initial + one expansion
    assert mock_llm.generate_json.call_count == 2


def test_estimate_duration_from_words():
    assert estimate_duration_from_words(140, wpm=140) == 60
    assert count_words("Hello, world — it's fine.") == 4
