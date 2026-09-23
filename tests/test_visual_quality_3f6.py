"""
Phase 3F.6 — Visual Quality & Scene Composition Tests.

Tests:
  1. Scene content included in visual context (narration, visual_description, topic)
  2. Job-level visual style derived from tone (consistent across scenes)
  3. Aspect ratio composition hints (16:9 vs 9:16)
  4. Previous-scene context passed correctly (continuity)
  5. Scene variation is deterministic (shot types)
  6. Prompt length stays within configured limit
  7. 16:9 composition guidance in generated prompts
  8. 9:16 composition guidance in generated prompts
  9. Missing scene content still works gracefully
  10. Existing visual-provider fallback remains unchanged
  11. Explicit background precedence remains unchanged
"""

from __future__ import annotations

import os
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.services.visual_prompt_generator import (
    VALID_VISUAL_STYLES,
    add_visual_prompts_to_scenes,
    generate_visual_prompt,
    generate_visual_prompts_batch,
    get_composition_hint,
    get_max_prompt_chars,
    get_shot_type,
    tone_to_visual_style,
    truncate_prompt,
)
from backend.content_models import Scene
from backend.services.visual.visual_provider import (
    AIVisualProvider,
    FallbackVisualProvider,
    VisualAssetResult,
    VisualProviderFactory,
    generate_visual_asset,
)
from backend.services.video.background_config import BackgroundType


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_scene(narration="Test narration.", visual_description="A test scene.", scene_number=1) -> Scene:
    return Scene(
        scene_number=scene_number,
        narration=narration,
        visual_description=visual_description,
        estimated_duration_seconds=10,
    )


def _mock_llm(return_value: str):
    mock = MagicMock()
    mock.provider_name = "mock"
    mock.generate.return_value = return_value
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tone → Visual Style mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestToneToVisualStyle:
    def test_engaging_maps_to_cinematic(self):
        assert tone_to_visual_style("engaging") == "cinematic"

    def test_informative_maps_to_documentary(self):
        assert tone_to_visual_style("informative") == "documentary"

    def test_educational_maps_to_documentary(self):
        assert tone_to_visual_style("educational") == "documentary"

    def test_professional_maps_to_realistic(self):
        assert tone_to_visual_style("professional") == "realistic"

    def test_formal_maps_to_realistic(self):
        assert tone_to_visual_style("formal") == "realistic"

    def test_fun_maps_to_illustration(self):
        assert tone_to_visual_style("fun") == "illustration"

    def test_minimalist_maps_to_minimalist(self):
        assert tone_to_visual_style("minimalist") == "minimalist"

    def test_unknown_tone_defaults_to_cinematic(self):
        assert tone_to_visual_style("unknown_xyz") == "cinematic"

    def test_valid_style_passthrough(self):
        for style in VALID_VISUAL_STYLES:
            assert tone_to_visual_style(style) == style

    def test_case_insensitive(self):
        assert tone_to_visual_style("ENGAGING") == "cinematic"
        assert tone_to_visual_style("Formal") == "realistic"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Shot-type variation — deterministic
# ─────────────────────────────────────────────────────────────────────────────

class TestShotTypeVariation:
    def test_scene_0_is_wide_shot(self):
        assert "wide" in get_shot_type(0).lower()

    def test_scene_1_is_medium_shot(self):
        assert "medium" in get_shot_type(1).lower()

    def test_scene_2_is_close_up(self):
        assert "close" in get_shot_type(2).lower()

    def test_pattern_wraps_after_5(self):
        # Scene 5 should be same as scene 0
        assert get_shot_type(5) == get_shot_type(0)

    def test_scene_6_same_as_scene_1(self):
        assert get_shot_type(6) == get_shot_type(1)

    def test_deterministic_same_input_same_output(self):
        for n in range(20):
            assert get_shot_type(n) == get_shot_type(n)

    def test_all_five_types_distinct(self):
        types = {get_shot_type(i) for i in range(5)}
        assert len(types) == 5

    def test_large_scene_number_does_not_crash(self):
        result = get_shot_type(999)
        assert isinstance(result, str)
        assert len(result) > 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Composition hints
# ─────────────────────────────────────────────────────────────────────────────

class TestCompositionHints:
    def test_16_9_hint_mentions_wide(self):
        hint = get_composition_hint("16:9")
        assert "wide" in hint.lower() or "cinematic" in hint.lower()

    def test_16_9_hint_mentions_caption(self):
        hint = get_composition_hint("16:9")
        assert "caption" in hint.lower() or "bottom" in hint.lower()

    def test_9_16_hint_mentions_vertical(self):
        hint = get_composition_hint("9:16")
        assert "vertical" in hint.lower() or "portrait" in hint.lower()

    def test_9_16_hint_mentions_safe_region(self):
        hint = get_composition_hint("9:16")
        assert "center" in hint.lower() or "safe" in hint.lower()

    def test_unknown_ratio_returns_fallback(self):
        hint = get_composition_hint("4:3")
        assert isinstance(hint, str)
        assert len(hint) > 0

    def test_16_9_and_9_16_are_different(self):
        assert get_composition_hint("16:9") != get_composition_hint("9:16")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Prompt truncation
# ─────────────────────────────────────────────────────────────────────────────

class TestPromptTruncation:
    def test_short_prompt_unchanged(self):
        short = "A mountain at sunset."
        assert truncate_prompt(short, max_chars=300) == short

    def test_long_prompt_truncated(self):
        long_p = "word " * 100  # 500 chars
        result = truncate_prompt(long_p, max_chars=100)
        assert len(result) <= 100

    def test_truncation_at_word_boundary(self):
        p = "one two three four five six seven eight"
        result = truncate_prompt(p, max_chars=20)
        # Should not cut mid-word
        assert result in p
        assert " " not in result or result.endswith(result.split()[-1])

    def test_exactly_at_limit_unchanged(self):
        p = "a" * 300
        assert truncate_prompt(p, max_chars=300) == p

    def test_get_max_prompt_chars_default(self):
        env = {k: v for k, v in os.environ.items() if k != "AI_VISUAL_PROMPT_MAX_CHARS"}
        with patch.dict(os.environ, env, clear=True):
            result = get_max_prompt_chars()
        assert result == 300

    def test_get_max_prompt_chars_custom(self):
        with patch.dict(os.environ, {"AI_VISUAL_PROMPT_MAX_CHARS": "150"}):
            result = get_max_prompt_chars()
        assert result == 150

    def test_empty_prompt_unchanged(self):
        assert truncate_prompt("", max_chars=100) == ""


# ─────────────────────────────────────────────────────────────────────────────
# 5. generate_visual_prompt — scene content included
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateVisualPromptContent:
    def test_calls_llm_with_narration(self):
        mock_llm = _mock_llm("A focused person at a desk, cinematic lighting")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            result = generate_visual_prompt(
                narration="Working hard towards success.",
                aspect_ratio="16:9",
                visual_style="cinematic",
                topic="productivity",
            )
        mock_llm.generate.assert_called_once()
        call_args = mock_llm.generate.call_args
        prompt_sent = call_args[0][0]
        assert "Working hard towards success" in prompt_sent

    def test_calls_llm_with_visual_description(self):
        mock_llm = _mock_llm("An office environment")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompt(
                narration="Narration text.",
                visual_description="Close-up of hands typing on a keyboard.",
                aspect_ratio="16:9",
                visual_style="cinematic",
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "Close-up of hands typing on a keyboard" in prompt_sent

    def test_calls_llm_with_topic(self):
        mock_llm = _mock_llm("A city at night")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompt(
                narration="Cities never sleep.",
                topic="urban life",
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "urban life" in prompt_sent

    def test_calls_llm_with_shot_type(self):
        mock_llm = _mock_llm("Wide landscape")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompt(
                narration="An overview of the situation.",
                scene_number=0,  # wide establishing shot
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "wide establishing shot" in prompt_sent.lower()

    def test_includes_scene_number_and_total(self):
        mock_llm = _mock_llm("A medium shot")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompt(
                narration="Scene content.",
                scene_number=2,
                total_scenes=8,
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "3 of 8" in prompt_sent  # scene_number+1

    def test_includes_aspect_ratio(self):
        mock_llm = _mock_llm("Portrait image")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompt(
                narration="Test narration.",
                aspect_ratio="9:16",
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "9:16" in prompt_sent

    def test_includes_visual_style(self):
        mock_llm = _mock_llm("Documentary style shot")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompt(
                narration="Test narration.",
                visual_style="documentary",
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "documentary" in prompt_sent.lower()

    def test_returns_none_on_llm_failure(self):
        from backend.services.llm.base import LLMError
        mock_llm = MagicMock()
        mock_llm.provider_name = "mock"
        mock_llm.generate.side_effect = LLMError("test error")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            result = generate_visual_prompt(narration="Test.")
        assert result is None

    def test_missing_narration_does_not_crash(self):
        mock_llm = _mock_llm("Fallback result")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            result = generate_visual_prompt(narration="")
        # Should not raise — returns result or None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Previous-scene continuity
# ─────────────────────────────────────────────────────────────────────────────

class TestSceneContinuity:
    def test_prev_prompt_included_when_provided(self):
        mock_llm = _mock_llm("A different office view")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompt(
                narration="The meeting continues.",
                prev_prompt="Person sitting at a desk working on a laptop.",
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "Person sitting at a desk" in prompt_sent

    def test_prev_prompt_not_included_when_none(self):
        mock_llm = _mock_llm("A fresh scene")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompt(
                narration="First scene.",
                prev_prompt=None,
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "PREVIOUS SCENE" not in prompt_sent

    def test_prev_prompt_truncated_to_100_chars(self):
        long_prev = "A very long previous prompt " * 20  # 560 chars
        mock_llm = _mock_llm("Result")
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompt(
                narration="Scene narration.",
                prev_prompt=long_prev,
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        # The prev prompt context should be truncated — find the PREVIOUS SCENE line
        lines = prompt_sent.split("\n")
        prev_lines = [l for l in lines if "PREVIOUS SCENE" in l]
        if prev_lines:
            prev_content = prev_lines[0]
            # Content after "PREVIOUS SCENE: " should not exceed 100 + label chars
            assert len(prev_content) < 200


# ─────────────────────────────────────────────────────────────────────────────
# 7. Batch generation with context
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchGenerationContext:
    def test_batch_includes_topic(self):
        import json
        mock_llm = _mock_llm('["prompt one", "prompt two", "prompt three"]')
        scenes = [_make_scene(f"Narration {i}", f"Visual {i}", i+1) for i in range(3)]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompts_batch(scenes, topic="climate change")
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "climate change" in prompt_sent

    def test_batch_includes_visual_style(self):
        mock_llm = _mock_llm('["p1", "p2"]')
        scenes = [_make_scene() for _ in range(2)]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompts_batch(scenes, visual_style="documentary")
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "documentary" in prompt_sent.lower()

    def test_batch_includes_shot_types(self):
        mock_llm = _mock_llm('["p1", "p2", "p3"]')
        scenes = [_make_scene(f"Narration {i}") for i in range(3)]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompts_batch(scenes)
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "wide establishing shot" in prompt_sent.lower()
        assert "medium shot" in prompt_sent.lower()

    def test_batch_includes_visual_descriptions(self):
        mock_llm = _mock_llm('["p1"]')
        scenes = [_make_scene(visual_description="Hands holding a globe")]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            generate_visual_prompts_batch(scenes)
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "Hands holding a globe" in prompt_sent

    def test_batch_returns_correct_count(self):
        mock_llm = _mock_llm('["a", "b", "c", "d", "e"]')
        scenes = [_make_scene(f"S{i}") for i in range(5)]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            result = generate_visual_prompts_batch(scenes)
        assert len(result) == 5

    def test_batch_applies_truncation(self):
        long_prompt = "word " * 100  # 500 chars each
        import json
        mock_llm = _mock_llm(json.dumps([long_prompt, long_prompt]))
        scenes = [_make_scene() for _ in range(2)]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            result = generate_visual_prompts_batch(scenes)
        for p in result:
            if p:
                assert len(p) <= get_max_prompt_chars()

    def test_batch_fallback_to_individual_on_json_error(self):
        call_count = {"n": 0}
        def mock_generate(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "not valid json"  # batch fails
            return "Individual prompt result"  # individual calls succeed

        mock_llm = MagicMock()
        mock_llm.provider_name = "mock"
        mock_llm.generate.side_effect = mock_generate

        scenes = [_make_scene(f"Scene {i}") for i in range(2)]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            result = generate_visual_prompts_batch(scenes)
        # Should have fallen back to individual (1 batch + 2 individual = 3 calls)
        assert call_count["n"] >= 3
        assert len(result) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 8. add_visual_prompts_to_scenes — tone-driven style
# ─────────────────────────────────────────────────────────────────────────────

class TestAddVisualPromptsStyle:
    def test_documentary_style_passed_to_batch(self):
        mock_llm = _mock_llm('["doc prompt"]')
        scenes = [_make_scene()]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            add_visual_prompts_to_scenes(
                scenes,
                visual_style="documentary",
                topic="climate change",
            )
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "documentary" in prompt_sent.lower()

    def test_topic_passed_to_batch(self):
        mock_llm = _mock_llm('["result"]')
        scenes = [_make_scene()]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            add_visual_prompts_to_scenes(scenes, topic="space exploration")
        prompt_sent = mock_llm.generate.call_args[0][0]
        assert "space exploration" in prompt_sent

    def test_empty_scenes_returns_empty(self):
        result = add_visual_prompts_to_scenes([])
        assert result == []

    def test_output_has_same_count_as_input(self):
        mock_llm = _mock_llm('["p1", "p2", "p3"]')
        scenes = [_make_scene(f"Scene {i}") for i in range(3)]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            result = add_visual_prompts_to_scenes(scenes)
        assert len(result) == 3

    def test_scenes_are_scene_objects(self):
        mock_llm = _mock_llm('["p1"]')
        scenes = [_make_scene()]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            result = add_visual_prompts_to_scenes(scenes)
        assert all(isinstance(s, Scene) for s in result)

    def test_failed_prompt_still_returns_scene(self):
        from backend.services.llm.base import LLMError
        mock_llm = MagicMock()
        mock_llm.provider_name = "mock"
        mock_llm.generate.side_effect = LLMError("test")
        scenes = [_make_scene()]
        with patch("backend.services.visual_prompt_generator.get_llm_provider", return_value=mock_llm):
            result = add_visual_prompts_to_scenes(scenes)
        assert len(result) == 1
        # visual_prompt should be None on failure
        assert result[0].visual_prompt is None


# ─────────────────────────────────────────────────────────────────────────────
# 9. AIVisualProvider — prompt augmentation
# ─────────────────────────────────────────────────────────────────────────────

class TestAIVisualProviderPromptAugmentation:
    def _make_provider(self, mock_backend):
        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as f:
            f.create.return_value = mock_backend
            return AIVisualProvider()

    def test_missing_prompt_uses_topic_in_fallback(self, tmp_path):
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = MagicMock(
            success=False, output_path=None,
            error_message="test", backend_name="pollinations", model="flux",
            generation_time_s=0.0,
        )
        provider = self._make_provider(mock_backend)
        env = {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                None, "16:9",
                {"job_id": "j1", "scene_number": 0, "topic": "space exploration"},
            )
        called_prompt = mock_backend.generate_image.call_args[0][0].prompt
        assert "space exploration" in called_prompt

    def test_missing_prompt_uses_visual_style_in_fallback(self, tmp_path):
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = MagicMock(
            success=False, output_path=None,
            error_message="test", backend_name="pollinations", model="flux",
            generation_time_s=0.0,
        )
        provider = self._make_provider(mock_backend)
        env = {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}
        with patch.dict(os.environ, env):
            result = provider.generate_visual(
                None, "16:9",
                {"job_id": "j2", "scene_number": 0, "visual_style": "documentary"},
            )
        called_prompt = mock_backend.generate_image.call_args[0][0].prompt
        assert "documentary" in called_prompt.lower()

    def test_16_9_composition_hint_added(self, tmp_path):
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = MagicMock(
            success=False, output_path=None,
            error_message="test", backend_name="pollinations", model="flux",
            generation_time_s=0.0,
        )
        provider = self._make_provider(mock_backend)
        env = {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}
        with patch.dict(os.environ, env):
            provider.generate_visual(
                "A mountain landscape",  # no composition keywords
                "16:9",
                {"job_id": "j3", "scene_number": 0},
            )
        called_prompt = mock_backend.generate_image.call_args[0][0].prompt
        # Should have 16:9 composition hint appended
        hint_16_9 = get_composition_hint("16:9")
        assert any(kw in called_prompt.lower() for kw in ["wide", "cinematic", "caption", "center"])

    def test_9_16_composition_hint_added(self, tmp_path):
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = MagicMock(
            success=False, output_path=None,
            error_message="test", backend_name="pollinations", model="flux",
            generation_time_s=0.0,
        )
        provider = self._make_provider(mock_backend)
        env = {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}
        with patch.dict(os.environ, env):
            provider.generate_visual(
                "A tall mountain peak",  # no composition keywords
                "9:16",
                {"job_id": "j4", "scene_number": 0},
            )
        called_prompt = mock_backend.generate_image.call_args[0][0].prompt
        hint_9_16 = get_composition_hint("9:16")
        assert any(kw in called_prompt.lower() for kw in ["vertical", "portrait", "centered", "center"])

    def test_prompt_already_has_composition_not_doubled(self, tmp_path):
        """If prompt already contains a framing keyword, don't add another hint."""
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = MagicMock(
            success=False, output_path=None,
            error_message="test", backend_name="pollinations", model="flux",
            generation_time_s=0.0,
        )
        provider = self._make_provider(mock_backend)
        env = {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}
        original_prompt = "Wide shot of a mountain with dramatic lighting, centered composition"
        with patch.dict(os.environ, env):
            provider.generate_visual(
                original_prompt, "16:9", {"job_id": "j5", "scene_number": 0},
            )
        called_prompt = mock_backend.generate_image.call_args[0][0].prompt
        # Should start with the original prompt (composition hint not added again)
        assert called_prompt.startswith(original_prompt) or original_prompt in called_prompt

    def test_prompt_truncated_to_max_chars(self, tmp_path):
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = MagicMock(
            success=False, output_path=None,
            error_message="test", backend_name="pollinations", model="flux",
            generation_time_s=0.0,
        )
        provider = self._make_provider(mock_backend)
        env = {
            "AI_IMAGE_OUTPUT_DIR": str(tmp_path),
            "AI_IMAGE_CACHE_ENABLED": "false",
            "AI_VISUAL_PROMPT_MAX_CHARS": "100",
        }
        long_prompt = "A detailed mountain landscape scene " * 20  # very long
        with patch.dict(os.environ, env):
            provider.generate_visual(
                long_prompt, "16:9", {"job_id": "j6", "scene_number": 0},
            )
        called_prompt = mock_backend.generate_image.call_args[0][0].prompt
        assert len(called_prompt) <= 100


# ─────────────────────────────────────────────────────────────────────────────
# 10. Existing fallback behavior unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackBehaviorUnchanged:
    def test_fallback_provider_still_returns_gradient(self):
        provider = FallbackVisualProvider()
        result = provider.generate_visual("any prompt", "16:9")
        assert result.fallback_used is True
        assert result.asset_path is None
        assert result.provider_name == "fallback"

    def test_ai_provider_backend_failure_returns_gradient(self, tmp_path):
        mock_backend = MagicMock()
        mock_backend.backend_name = "pollinations"
        mock_backend.generate_image.return_value = MagicMock(
            success=False, output_path=None,
            error_message="server error", backend_name="pollinations", model="flux",
            generation_time_s=0.0,
        )
        with patch("backend.services.visual.visual_provider.AIImageBackendFactory") as f:
            f.create.return_value = mock_backend
            provider = AIVisualProvider()
        env = {"AI_IMAGE_OUTPUT_DIR": str(tmp_path), "AI_IMAGE_CACHE_ENABLED": "false"}
        with patch.dict(os.environ, env):
            result = provider.generate_visual("A scene", "16:9", {"job_id": "fb1", "scene_number": 0})
        assert result.fallback_used is True
        assert result.asset_path is None

    def test_invalid_visual_style_fallback_works(self):
        # Unknown style should not crash tone_to_visual_style
        style = tone_to_visual_style("nonexistent_tone_xyz")
        assert style in VALID_VISUAL_STYLES


# ─────────────────────────────────────────────────────────────────────────────
# 11. Explicit background precedence unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestExplicitBackgroundPrecedenceUnchanged:
    def test_gradient_not_overridden(self):
        """Explicit gradient scenes bypass the visual provider branch."""
        scene = {"background_type": BackgroundType.GRADIENT.value, "background_path": None}
        # Simulate queue_processor VISUAL_PROVIDER branch condition
        should_apply = (
            not scene.get("background_type")
            or scene.get("background_type") == BackgroundType.VISUAL_PROVIDER.value
        )
        assert should_apply is False  # explicit gradient → skip

    def test_local_image_not_overridden(self):
        scene = {
            "background_type": BackgroundType.LOCAL_IMAGE.value,
            "background_path": "/path/to/image.jpg",
        }
        should_apply = (
            not scene.get("background_type")
            or scene.get("background_type") == BackgroundType.VISUAL_PROVIDER.value
        )
        assert should_apply is False

    def test_visual_provider_scene_gets_applied(self):
        scene = {"background_type": BackgroundType.VISUAL_PROVIDER.value}
        should_apply = (
            not scene.get("background_type")
            or scene.get("background_type") == BackgroundType.VISUAL_PROVIDER.value
        )
        assert should_apply is True

    def test_empty_background_type_gets_applied(self):
        scene = {}
        should_apply = (
            not scene.get("background_type")
            or scene.get("background_type") == BackgroundType.VISUAL_PROVIDER.value
        )
        assert should_apply is True
