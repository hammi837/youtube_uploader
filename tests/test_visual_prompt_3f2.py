"""
Phase 3F.2: Visual Prompt Generation Tests.

Tests for scene visual prompt generation using LLM abstraction.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.content_models import Scene
from backend.services.visual_prompt_generator import (
    add_visual_prompts_to_scenes,
    generate_visual_prompt,
    generate_visual_prompts_batch,
)


class TestSinglePromptGeneration:
    """Test single scene visual prompt generation."""

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_generate_visual_prompt_success(self, mock_get_llm):
        """Test successful visual prompt generation."""
        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.return_value = "A dramatic sunset over mountains"
        mock_get_llm.return_value = mock_llm

        narration = "The sun sets behind the mountains"
        prompt = generate_visual_prompt(narration, aspect_ratio="16:9", visual_style="cinematic")

        assert prompt == "A dramatic sunset over mountains"
        mock_llm.generate.assert_called_once()

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_generate_visual_prompt_with_quotes(self, mock_get_llm):
        """Test that quotes are stripped from generated prompts."""
        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.return_value = '"A dramatic sunset over mountains"'
        mock_get_llm.return_value = mock_llm

        narration = "The sun sets behind the mountains"
        prompt = generate_visual_prompt(narration)

        assert prompt == "A dramatic sunset over mountains"

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_generate_visual_prompt_llm_error(self, mock_get_llm):
        """Test that LLM errors return None instead of raising."""
        from backend.services.llm.base import LLMError

        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.side_effect = LLMError("API error")
        mock_get_llm.return_value = mock_llm

        narration = "The sun sets behind the mountains"
        prompt = generate_visual_prompt(narration)

        assert prompt is None

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_generate_visual_prompt_empty_result(self, mock_get_llm):
        """Test that empty results return None."""
        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.return_value = ""
        mock_get_llm.return_value = mock_llm

        narration = "The sun sets behind the mountains"
        prompt = generate_visual_prompt(narration)

        assert prompt is None

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_generate_visual_prompt_aspect_ratio_context(self, mock_get_llm):
        """Test that aspect ratio is included in generation context."""
        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.return_value = "Vertical portrait composition"
        mock_get_llm.return_value = mock_llm

        narration = "A person standing"
        prompt = generate_visual_prompt(narration, aspect_ratio="9:16")

        assert prompt == "Vertical portrait composition"
        # Verify the prompt includes aspect ratio context
        call_args = mock_llm.generate.call_args
        assert "9:16" in call_args[0][0]

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_generate_visual_prompt_style_context(self, mock_get_llm):
        """Test that visual style is included in generation context."""
        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.return_value = "Documentary style street scene"
        mock_get_llm.return_value = mock_llm

        narration = "Busy city street"
        prompt = generate_visual_prompt(narration, visual_style="documentary")

        assert prompt == "Documentary style street scene"
        # Verify the prompt includes style context
        call_args = mock_llm.generate.call_args
        assert "documentary" in call_args[0][0]


class TestBatchPromptGeneration:
    """Test batch visual prompt generation for multiple scenes."""

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_batch_generation_success(self, mock_get_llm):
        """Test successful batch generation of multiple prompts."""
        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.return_value = '["Prompt 1", "Prompt 2", "Prompt 3"]'
        mock_get_llm.return_value = mock_llm

        scenes = [
            Scene(scene_number=1, narration="Scene 1", visual_description="Desc 1", estimated_duration_seconds=10),
            Scene(scene_number=2, narration="Scene 2", visual_description="Desc 2", estimated_duration_seconds=10),
            Scene(scene_number=3, narration="Scene 3", visual_description="Desc 3", estimated_duration_seconds=10),
        ]

        prompts = generate_visual_prompts_batch(scenes, aspect_ratio="16:9")

        assert len(prompts) == 3
        assert prompts == ["Prompt 1", "Prompt 2", "Prompt 3"]

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_batch_generation_json_parse_error(self, mock_get_llm):
        """Test that JSON parse errors fall back to individual generation."""
        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.side_effect = [
            "not valid json",  # Batch generation fails
            "Prompt 1",  # Individual fallback
            "Prompt 2",
            "Prompt 3",
        ]
        mock_get_llm.return_value = mock_llm

        scenes = [
            Scene(scene_number=1, narration="Scene 1", visual_description="Desc 1", estimated_duration_seconds=10),
            Scene(scene_number=2, narration="Scene 2", visual_description="Desc 2", estimated_duration_seconds=10),
            Scene(scene_number=3, narration="Scene 3", visual_description="Desc 3", estimated_duration_seconds=10),
        ]

        prompts = generate_visual_prompts_batch(scenes)

        assert len(prompts) == 3
        assert prompts == ["Prompt 1", "Prompt 2", "Prompt 3"]

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_batch_generation_llm_error(self, mock_get_llm):
        """Test that LLM errors fall back to individual generation."""
        from backend.services.llm.base import LLMError

        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.side_effect = [
            LLMError("API error"),  # Batch generation fails
            "Prompt 1",  # Individual fallback
            "Prompt 2",
            "Prompt 3",
        ]
        mock_get_llm.return_value = mock_llm

        scenes = [
            Scene(scene_number=1, narration="Scene 1", visual_description="Desc 1", estimated_duration_seconds=10),
            Scene(scene_number=2, narration="Scene 2", visual_description="Desc 2", estimated_duration_seconds=10),
            Scene(scene_number=3, narration="Scene 3", visual_description="Desc 3", estimated_duration_seconds=10),
        ]

        prompts = generate_visual_prompts_batch(scenes)

        assert len(prompts) == 3
        assert prompts == ["Prompt 1", "Prompt 2", "Prompt 3"]

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_batch_generation_empty_scenes(self, mock_get_llm):
        """Test batch generation with empty scene list."""
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        prompts = generate_visual_prompts_batch([])

        assert prompts == []
        mock_llm.generate.assert_not_called()

    @patch("backend.services.visual_prompt_generator.get_llm_provider")
    def test_batch_generation_fewer_prompts_than_scenes(self, mock_get_llm):
        """Test that fewer prompts than scenes results in None padding."""
        mock_llm = MagicMock()
        mock_llm.provider_name = "test_provider"
        mock_llm.generate.return_value = '["Prompt 1", "Prompt 2"]'  # Only 2 prompts for 3 scenes
        mock_get_llm.return_value = mock_llm

        scenes = [
            Scene(scene_number=1, narration="Scene 1", visual_description="Desc 1", estimated_duration_seconds=10),
            Scene(scene_number=2, narration="Scene 2", visual_description="Desc 2", estimated_duration_seconds=10),
            Scene(scene_number=3, narration="Scene 3", visual_description="Desc 3", estimated_duration_seconds=10),
        ]

        prompts = generate_visual_prompts_batch(scenes)

        assert len(prompts) == 3
        assert prompts[0] == "Prompt 1"
        assert prompts[1] == "Prompt 2"
        assert prompts[2] is None  # Padded with None


class TestAddVisualPromptsToScenes:
    """Test adding visual prompts to scene objects."""

    @patch("backend.services.visual_prompt_generator.generate_visual_prompts_batch")
    def test_add_prompts_batch_mode(self, mock_batch):
        """Test adding prompts in batch mode."""
        mock_batch.return_value = ["Prompt 1", "Prompt 2", "Prompt 3"]

        scenes = [
            Scene(scene_number=1, narration="Scene 1", visual_description="Desc 1", estimated_duration_seconds=10),
            Scene(scene_number=2, narration="Scene 2", visual_description="Desc 2", estimated_duration_seconds=10),
            Scene(scene_number=3, narration="Scene 3", visual_description="Desc 3", estimated_duration_seconds=10),
        ]

        updated = add_visual_prompts_to_scenes(scenes, use_batch=True)

        assert len(updated) == 3
        assert updated[0].visual_prompt == "Prompt 1"
        assert updated[1].visual_prompt == "Prompt 2"
        assert updated[2].visual_prompt == "Prompt 3"
        mock_batch.assert_called_once()

    @patch("backend.services.visual_prompt_generator.generate_visual_prompt")
    def test_add_prompts_individual_mode(self, mock_single):
        """Test adding prompts in individual mode."""
        mock_single.side_effect = ["Prompt 1", "Prompt 2", "Prompt 3"]

        scenes = [
            Scene(scene_number=1, narration="Scene 1", visual_description="Desc 1", estimated_duration_seconds=10),
            Scene(scene_number=2, narration="Scene 2", visual_description="Desc 2", estimated_duration_seconds=10),
            Scene(scene_number=3, narration="Scene 3", visual_description="Desc 3", estimated_duration_seconds=10),
        ]

        updated = add_visual_prompts_to_scenes(scenes, use_batch=False)

        assert len(updated) == 3
        assert updated[0].visual_prompt == "Prompt 1"
        assert updated[1].visual_prompt == "Prompt 2"
        assert updated[2].visual_prompt == "Prompt 3"
        assert mock_single.call_count == 3

    @patch("backend.services.visual_prompt_generator.generate_visual_prompts_batch")
    def test_add_prompts_with_none_failures(self, mock_batch):
        """Test that failed prompt generations result in None values."""
        mock_batch.return_value = ["Prompt 1", None, "Prompt 3"]

        scenes = [
            Scene(scene_number=1, narration="Scene 1", visual_description="Desc 1", estimated_duration_seconds=10),
            Scene(scene_number=2, narration="Scene 2", visual_description="Desc 2", estimated_duration_seconds=10),
            Scene(scene_number=3, narration="Scene 3", visual_description="Desc 3", estimated_duration_seconds=10),
        ]

        updated = add_visual_prompts_to_scenes(scenes)

        assert len(updated) == 3
        assert updated[0].visual_prompt == "Prompt 1"
        assert updated[1].visual_prompt is None
        assert updated[2].visual_prompt == "Prompt 3"

    @patch("backend.services.visual_prompt_generator.generate_visual_prompts_batch")
    def test_add_prompts_empty_scenes(self, mock_batch):
        """Test adding prompts to empty scene list."""
        mock_batch.return_value = []

        updated = add_visual_prompts_to_scenes([])

        assert updated == []
        mock_batch.assert_not_called()

    @patch("backend.services.visual_prompt_generator.generate_visual_prompts_batch")
    def test_add_prompts_preserves_other_fields(self, mock_batch):
        """Test that adding prompts preserves other scene fields."""
        mock_batch.return_value = ["Prompt 1"]

        scenes = [
            Scene(
                scene_number=1,
                narration="Scene 1",
                visual_description="Desc 1",
                estimated_duration_seconds=10,
                background_type="gradient",
                background_path=None,
                background_color="#000000",
                background_fit="cover",
            ),
        ]

        updated = add_visual_prompts_to_scenes(scenes)

        assert len(updated) == 1
        assert updated[0].visual_prompt == "Prompt 1"
        assert updated[0].narration == "Scene 1"
        assert updated[0].visual_description == "Desc 1"
        assert updated[0].background_type == "gradient"
        assert updated[0].background_fit == "cover"


class TestBackwardCompatibility:
    """Test backward compatibility with existing scenes."""

    def test_scene_without_visual_prompt(self):
        """Test that scenes without visual_prompt field are still valid."""
        scene = Scene(
            scene_number=1,
            narration="Test narration",
            visual_description="Test description",
            estimated_duration_seconds=10,
        )

        # visual_prompt should default to None
        assert scene.visual_prompt is None

    def test_scene_with_none_visual_prompt(self):
        """Test that scenes with None visual_prompt are still valid."""
        scene = Scene(
            scene_number=1,
            narration="Test narration",
            visual_description="Test description",
            estimated_duration_seconds=10,
            visual_prompt=None,
        )

        assert scene.visual_prompt is None

    def test_scene_serialization_with_visual_prompt(self):
        """Test that scenes with visual_prompt serialize correctly."""
        scene = Scene(
            scene_number=1,
            narration="Test narration",
            visual_description="Test description",
            estimated_duration_seconds=10,
            visual_prompt="Test visual prompt",
        )

        # Serialize to dict
        scene_dict = scene.model_dump()
        assert "visual_prompt" in scene_dict
        assert scene_dict["visual_prompt"] == "Test visual prompt"

        # Deserialize back
        scene2 = Scene(**scene_dict)
        assert scene2.visual_prompt == "Test visual prompt"
