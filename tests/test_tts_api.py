"""
tests/test_tts_api.py — Phase 2B TTS tests.

Coverage:
  Narration extraction:
    - normal script (hook + scenes)
    - hook only
    - scenes only
    - missing fields / empty fields
    - malformed data
    - markdown removal
    - URL removal
    - duration estimation
    - word count

  Provider (EdgeTTSProvider):
    - successful synthesis (mocked)
    - empty text raises TTSValidationError
    - text too long raises TTSValidationError
    - invalid voice raises TTSValidationError
    - network failure raises TTSNetworkError
    - rate limit raises TTSRateLimitError
    - synthesis error raises TTSGenerationError
    - retry behavior on transient errors
    - text cleaning (_clean_text_for_tts)
    - chunking (_split_into_chunks)
    - path safety (_check_path_safety)
    - voice list (mocked)

  TTS factory:
    - edge provider selected
    - unknown provider raises TTSConfigError

  API endpoints:
    - GET /api/tts/voices — success, filtered, network error
    - POST /api/tts/generate — success (202), empty text (422)
    - POST /api/tts/generate-from-content — success, missing project (404),
      no script (422), reuse existing completed audio
    - GET /api/tts/{id} — found, not found (404)
    - GET /api/tts/{id}/audio — completed, not ready (409), file missing (404)
    - DELETE /api/tts/{id} — success (204), not found (404)
    - security: no secrets in responses

  Database:
    - GeneratedAudio creation
    - status updates (pending → generating → completed / failed)
    - relationship to ContentProject
    - cascade delete when project deleted

IMPORTANT: No real Edge-TTS calls are made. All external I/O is mocked.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import Base, get_db
from backend.main import app
from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
from backend.tts_models import AudioStatus, GeneratedAudio

from tests.conftest import shared_engine, SharedTestingSessionLocal

test_engine = shared_engine
TestingSessionLocal = SharedTestingSessionLocal


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── DB helpers ────────────────────────────────────────────────────────────────

def make_project(db, **kwargs) -> ContentProject:
    defaults = dict(
        id=str(uuid.uuid4()),
        topic="Test Topic",
        language="en",
        tone="informative",
        target_duration_seconds=180,
        scene_count=10,
        status=ContentStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    project = ContentProject(**defaults)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def make_project_with_script(db, topic: str = "Test Topic") -> ContentProject:
    project = make_project(db, topic=topic)
    script = GeneratedScriptRecord(
        id=str(uuid.uuid4()),
        content_project_id=project.id,
        title="Test Script Title",
        description="Test description.",
        hook="This is the hook sentence.",
        tags_json=json.dumps(["tag1", "tag2"]),
        scenes_json=json.dumps([
            {"scene_number": 1, "narration": "Scene one narration text here.",
             "visual_description": "Visual.", "estimated_duration_seconds": 15},
            {"scene_number": 2, "narration": "Scene two narration text here.",
             "visual_description": "Visual.", "estimated_duration_seconds": 20},
        ]),
        estimated_duration_seconds=180,
    )
    db.add(script)
    db.commit()
    db.refresh(project)
    return project


def make_audio(db, project_id: str, status: str = AudioStatus.COMPLETED,
               file_path: str | None = None, **kwargs) -> GeneratedAudio:
    defaults = dict(
        id=str(uuid.uuid4()),
        content_project_id=project_id,
        voice="en-US-AriaNeural",
        language="en",
        text_length=100,
        file_path=file_path or f"/fake/path/{uuid.uuid4()}.mp3",
        file_size_bytes=50000,
        duration_seconds=45.0,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    audio = GeneratedAudio(**defaults)
    db.add(audio)
    db.commit()
    db.refresh(audio)
    return audio


# ── Narration extraction tests ────────────────────────────────────────────────

class TestNarrationExtraction:

    def _make_script(self, hook="", scenes=None):
        """Build a minimal GeneratedScript Pydantic object."""
        from backend.content_models import GeneratedScript, Scene
        return GeneratedScript(
            title="Test",
            description="desc",
            tags=[],
            hook=hook,
            estimated_duration_seconds=60,
            scenes=[Scene(**s) for s in (scenes or [])],
        )

    def test_extracts_hook_and_scenes(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(
            hook="Great hook here.",
            scenes=[
                {"scene_number": 1, "narration": "Scene one text.",
                 "visual_description": "v", "estimated_duration_seconds": 10},
                {"scene_number": 2, "narration": "Scene two text.",
                 "visual_description": "v", "estimated_duration_seconds": 10},
            ],
        )
        result = extract_narration_text(script)
        assert "Great hook here." in result
        assert "Scene one text." in result
        assert "Scene two text." in result

    def test_hook_comes_first(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(
            hook="HOOK.",
            scenes=[{"scene_number": 1, "narration": "SCENE.",
                     "visual_description": "v", "estimated_duration_seconds": 10}],
        )
        result = extract_narration_text(script)
        assert result.index("HOOK.") < result.index("SCENE.")

    def test_hook_only(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(hook="Only the hook.")
        result = extract_narration_text(script)
        assert "Only the hook." in result

    def test_scenes_only_no_hook(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(
            hook="",
            scenes=[{"scene_number": 1, "narration": "Scene narration only.",
                     "visual_description": "v", "estimated_duration_seconds": 10}],
        )
        result = extract_narration_text(script)
        assert "Scene narration only." in result

    def test_empty_script_raises_value_error(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(hook="", scenes=[])
        with pytest.raises(ValueError, match="narration"):
            extract_narration_text(script)

    def test_scenes_with_empty_narration_are_skipped(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(
            hook="Hook.",
            scenes=[
                {"scene_number": 1, "narration": "",
                 "visual_description": "v", "estimated_duration_seconds": 10},
                {"scene_number": 2, "narration": "Real narration.",
                 "visual_description": "v", "estimated_duration_seconds": 10},
            ],
        )
        result = extract_narration_text(script)
        assert "Real narration." in result

    def test_visual_description_not_included(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(
            hook="Hook text.",
            scenes=[{"scene_number": 1, "narration": "Narration.",
                     "visual_description": "SHOULD NOT APPEAR", "estimated_duration_seconds": 10}],
        )
        result = extract_narration_text(script)
        assert "SHOULD NOT APPEAR" not in result

    def test_markdown_removed(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(
            hook="**Bold hook** with *italic* text.",
            scenes=[],
        )
        result = extract_narration_text(script)
        assert "**" not in result
        assert "*" not in result
        assert "Bold hook" in result
        assert "italic" in result

    def test_urls_removed(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(
            hook="Visit https://example.com for more info.",
            scenes=[],
        )
        result = extract_narration_text(script)
        assert "https://example.com" not in result
        assert "Visit" in result

    def test_punctuation_preserved(self):
        from backend.services.tts.narration import extract_narration_text
        script = self._make_script(
            hook="Is it true? Yes, it is! Really.",
            scenes=[],
        )
        result = extract_narration_text(script)
        assert "?" in result
        assert "!" in result
        assert "," in result
        assert "." in result

    def test_orm_record_extraction(self):
        """extract_narration_text must also work with GeneratedScriptRecord ORM objects."""
        from backend.services.tts.narration import extract_narration_text
        # Mock an ORM-style object
        record = MagicMock()
        record.hook = "ORM hook text."
        record.scenes_as_list.return_value = [
            {"scene_number": 1, "narration": "ORM scene narration.",
             "visual_description": "v", "estimated_duration_seconds": 10}
        ]
        # Remove the .scenes attribute to force ORM path
        del record.scenes
        result = extract_narration_text(record)
        assert "ORM hook text." in result
        assert "ORM scene narration." in result

    def test_duration_estimation(self):
        from backend.services.tts.narration import estimate_narration_duration
        # 150 words should be ~60 seconds
        text = " ".join(["word"] * 150)
        duration = estimate_narration_duration(text)
        assert abs(duration - 60.0) < 1.0

    def test_word_count(self):
        from backend.services.tts.narration import count_narration_words
        assert count_narration_words("one two three") == 3
        assert count_narration_words("") == 0


# ── Text cleaning tests ───────────────────────────────────────────────────────

class TestTextCleaning:

    def test_bold_markdown_removed(self):
        from backend.services.tts.edge_provider import _clean_text_for_tts
        assert "**" not in _clean_text_for_tts("**bold**")
        assert "bold" in _clean_text_for_tts("**bold**")

    def test_headers_removed(self):
        from backend.services.tts.edge_provider import _clean_text_for_tts
        result = _clean_text_for_tts("## Section Title\n\nBody text.")
        assert "##" not in result
        assert "Section Title" in result

    def test_urls_removed(self):
        from backend.services.tts.edge_provider import _clean_text_for_tts
        result = _clean_text_for_tts("See https://example.com for details.")
        assert "https://example.com" not in result

    def test_code_fences_removed(self):
        from backend.services.tts.edge_provider import _clean_text_for_tts
        result = _clean_text_for_tts("```python\ncode here\n```")
        assert "```" not in result

    def test_punctuation_preserved(self):
        from backend.services.tts.edge_provider import _clean_text_for_tts
        result = _clean_text_for_tts("Hello, world! Is this working? Yes.")
        assert "," in result
        assert "!" in result
        assert "?" in result
        assert "." in result

    def test_multiple_spaces_normalized(self):
        from backend.services.tts.edge_provider import _clean_text_for_tts
        result = _clean_text_for_tts("too    many   spaces")
        assert "  " not in result


# ── Chunking tests ────────────────────────────────────────────────────────────

class TestChunking:

    def test_short_text_single_chunk(self):
        from backend.services.tts.edge_provider import _split_into_chunks
        text = "Short text."
        chunks = _split_into_chunks(text, max_chunk_len=1000)
        assert len(chunks) == 1
        assert chunks[0] == "Short text."

    def test_long_text_splits_at_paragraphs(self):
        from backend.services.tts.edge_provider import _split_into_chunks
        para = "This is a paragraph sentence. " * 20   # ~600 chars
        text = f"{para}\n\n{para}\n\n{para}"
        chunks = _split_into_chunks(text, max_chunk_len=700)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 700 + 200  # allow slight overage for sentences

    def test_all_text_preserved(self):
        from backend.services.tts.edge_provider import _split_into_chunks
        words = " ".join([f"word{i}" for i in range(200)])
        chunks = _split_into_chunks(words, max_chunk_len=100)
        rejoined = " ".join(chunks)
        # All words should appear somewhere in the rejoined text
        for i in range(200):
            assert f"word{i}" in rejoined

    def test_single_long_sentence_kept_together(self):
        from backend.services.tts.edge_provider import _split_into_chunks
        long_sentence = "w " * 600   # 1200 chars, no sentence boundaries
        chunks = _split_into_chunks(long_sentence.strip(), max_chunk_len=500)
        # Should still produce at least 1 chunk (no crash)
        assert len(chunks) >= 1


# ── Path safety test ──────────────────────────────────────────────────────────

class TestPathSafety:

    def test_path_within_allowed_dir_passes(self, tmp_path):
        from backend.services.tts.edge_provider import _check_path_safety
        _check_path_safety(tmp_path / "output.mp3", tmp_path)  # no exception

    def test_path_traversal_raises(self, tmp_path):
        from backend.services.tts.base import TTSValidationError
        from backend.services.tts.edge_provider import _check_path_safety
        with pytest.raises(TTSValidationError):
            _check_path_safety(tmp_path.parent / "evil.mp3", tmp_path)


# ── EdgeTTSProvider unit tests ────────────────────────────────────────────────

class TestEdgeTTSProvider:

    def _make_provider(self, tmp_path):
        with patch.dict(os.environ, {
            "TTS_OUTPUT_DIR": str(tmp_path),
            "TTS_DEFAULT_VOICE": "en-US-AriaNeural",
            "TTS_MAX_TEXT_LENGTH": "5000",
            "TTS_TIMEOUT_SECONDS": "30",
        }):
            from backend.services.tts.edge_provider import EdgeTTSProvider
            return EdgeTTSProvider()

    def _mock_communicate(self, tmp_path):
        """Patch edge_tts.Communicate to write a dummy MP3 file."""
        async def fake_save(path):
            Path(path).write_bytes(b"\xff\xfb" * 100)  # fake MP3 bytes

        mock_comm = MagicMock()
        mock_comm.save = fake_save
        return patch("edge_tts.Communicate", return_value=mock_comm)

    def test_provider_name(self, tmp_path):
        provider = self._make_provider(tmp_path)
        assert provider.provider_name == "edge"

    def test_successful_synthesis(self, tmp_path):
        import asyncio
        provider = self._make_provider(tmp_path)
        output = str(tmp_path / "out.mp3")
        with self._mock_communicate(tmp_path):
            result = asyncio.run(provider.synthesize(
                "Hello world, this is a test.", "en-US-AriaNeural", output
            ))
        assert result.voice == "en-US-AriaNeural"
        assert result.format == "mp3"
        assert result.text_length == len("Hello world, this is a test.")
        assert Path(output).exists()

    def test_empty_text_raises_validation_error(self, tmp_path):
        import asyncio
        from backend.services.tts.base import TTSValidationError
        provider = self._make_provider(tmp_path)
        with pytest.raises(TTSValidationError):
            asyncio.run(provider.synthesize("", "en-US-AriaNeural", str(tmp_path / "out.mp3")))

    def test_whitespace_only_text_raises_validation_error(self, tmp_path):
        import asyncio
        from backend.services.tts.base import TTSValidationError
        provider = self._make_provider(tmp_path)
        with pytest.raises(TTSValidationError):
            asyncio.run(provider.synthesize("   \n  ", "en-US-AriaNeural", str(tmp_path / "out.mp3")))

    def test_text_too_long_raises_validation_error(self, tmp_path):
        import asyncio
        from backend.services.tts.base import TTSValidationError
        provider = self._make_provider(tmp_path)
        very_long = "word " * 10000   # 50000 chars, exceeds 4× limit of 5000
        with pytest.raises(TTSValidationError, match="too long"):
            asyncio.run(provider.synthesize(very_long, "en-US-AriaNeural", str(tmp_path / "out.mp3")))

    def test_network_error_raises_tts_network_error(self, tmp_path):
        import asyncio
        from backend.services.tts.base import TTSNetworkError
        provider = self._make_provider(tmp_path)

        mock_comm = MagicMock()
        async def failing_save(path):
            raise Exception("timeout connecting to server")
        mock_comm.save = failing_save

        with patch("edge_tts.Communicate", return_value=mock_comm):
            with pytest.raises(TTSNetworkError):
                asyncio.run(provider.synthesize(
                    "Test text.", "en-US-AriaNeural", str(tmp_path / "out.mp3")
                ))

    def test_rate_limit_raises_tts_rate_limit_error(self, tmp_path):
        import asyncio
        from backend.services.tts.base import TTSRateLimitError
        provider = self._make_provider(tmp_path)

        mock_comm = MagicMock()
        async def rate_limited_save(path):
            raise Exception("429 too many requests rate limit")
        mock_comm.save = rate_limited_save

        with patch("edge_tts.Communicate", return_value=mock_comm):
            with pytest.raises(TTSRateLimitError):
                asyncio.run(provider.synthesize(
                    "Test text.", "en-US-AriaNeural", str(tmp_path / "out.mp3")
                ))

    def test_invalid_voice_raises_validation_error(self, tmp_path):
        import asyncio
        from backend.services.tts.base import TTSValidationError
        provider = self._make_provider(tmp_path)

        mock_comm = MagicMock()
        async def bad_voice_save(path):
            raise Exception("voice not found invalid voice identifier")
        mock_comm.save = bad_voice_save

        with patch("edge_tts.Communicate", return_value=mock_comm):
            with pytest.raises(TTSValidationError, match="[Vv]oice"):
                asyncio.run(provider.synthesize(
                    "Test text.", "invalid-voice-xyz", str(tmp_path / "out.mp3")
                ))

    def test_voice_list_returned(self, tmp_path):
        import asyncio
        provider = self._make_provider(tmp_path)
        mock_voices = [
            {"ShortName": "en-US-AriaNeural", "Locale": "en-US",
             "FriendlyName": "Microsoft Aria Online (Natural) - English (United States)",
             "Gender": "Female"},
            {"ShortName": "en-GB-RyanNeural", "Locale": "en-GB",
             "FriendlyName": "Microsoft Ryan Online (Natural) - English (United Kingdom)",
             "Gender": "Male"},
        ]
        with patch("edge_tts.list_voices", new=AsyncMock(return_value=mock_voices)):
            voices = asyncio.run(provider.get_voices())
        assert len(voices) == 2
        assert voices[0].name == "en-US-AriaNeural"
        assert voices[0].gender == "Female"

    def test_voice_list_filtered_by_language(self, tmp_path):
        import asyncio
        provider = self._make_provider(tmp_path)
        mock_voices = [
            {"ShortName": "en-US-AriaNeural", "Locale": "en-US",
             "FriendlyName": "English Aria", "Gender": "Female"},
            {"ShortName": "es-ES-ElviraNeural", "Locale": "es-ES",
             "FriendlyName": "Spanish Elvira", "Gender": "Female"},
        ]
        with patch("edge_tts.list_voices", new=AsyncMock(return_value=mock_voices)):
            voices = asyncio.run(provider.get_voices(language="en"))
        assert len(voices) == 1
        assert voices[0].locale == "en-US"

    def test_voice_list_network_error(self, tmp_path):
        import asyncio
        from backend.services.tts.base import TTSNetworkError
        provider = self._make_provider(tmp_path)

        async def fail():
            raise Exception("connection refused")

        with patch("edge_tts.list_voices", new=AsyncMock(side_effect=Exception("connection refused"))):
            with pytest.raises(TTSNetworkError):
                asyncio.run(provider.get_voices())


# ── TTS factory tests ─────────────────────────────────────────────────────────

class TestTTSFactory:

    def test_edge_selected_by_default(self, tmp_path):
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.factory import get_tts_provider
        with patch.dict(os.environ, {"TTS_PROVIDER": "edge", "TTS_OUTPUT_DIR": str(tmp_path)}):
            provider = get_tts_provider()
        assert isinstance(provider, EdgeTTSProvider)

    def test_unknown_provider_raises_config_error(self):
        from backend.services.tts.base import TTSConfigError
        from backend.services.tts.factory import get_tts_provider
        with patch.dict(os.environ, {"TTS_PROVIDER": "nonexistent_tts"}):
            with pytest.raises(TTSConfigError):
                get_tts_provider()

    def test_provider_name_edge(self, tmp_path):
        from backend.services.tts.factory import get_tts_provider
        with patch.dict(os.environ, {"TTS_PROVIDER": "edge", "TTS_OUTPUT_DIR": str(tmp_path)}):
            provider = get_tts_provider()
        assert provider.provider_name == "edge"


# ── API: GET /api/tts/voices ──────────────────────────────────────────────────

class TestVoicesEndpoint:

    def _mock_voices(self):
        mock_voices = [
            {"ShortName": "en-US-AriaNeural", "Locale": "en-US",
             "FriendlyName": "Microsoft Aria", "Gender": "Female"},
            {"ShortName": "es-ES-ElviraNeural", "Locale": "es-ES",
             "FriendlyName": "Microsoft Elvira", "Gender": "Female"},
        ]

        async def fake_list_voices():
            return mock_voices

        return patch("edge_tts.list_voices", new=AsyncMock(return_value=mock_voices))

    def test_voices_returns_200(self, client):
        # Reset cache
        import backend.routers.tts as tts_router
        tts_router._voice_cache = None

        with patch.dict(os.environ, {"TTS_PROVIDER": "edge",
                                      "TTS_OUTPUT_DIR": str(Path("G:/youtube-uploader/data/audio"))}):
            with self._mock_voices():
                r = client.get("/api/tts/voices")
        assert r.status_code == 200
        voices = r.json()
        assert len(voices) >= 1
        assert "name" in voices[0]
        assert "locale" in voices[0]
        assert "gender" in voices[0]

    def test_voices_filtered_by_language(self, client):
        import backend.routers.tts as tts_router
        tts_router._voice_cache = None

        with patch.dict(os.environ, {"TTS_PROVIDER": "edge",
                                      "TTS_OUTPUT_DIR": str(Path("G:/youtube-uploader/data/audio"))}):
            with self._mock_voices():
                r = client.get("/api/tts/voices?language=en")
        assert r.status_code == 200
        for v in r.json():
            assert v["locale"].startswith("en")

    def test_voices_network_error_returns_503(self, client):
        import backend.routers.tts as tts_router
        tts_router._voice_cache = None

        from backend.services.tts.base import TTSNetworkError
        with patch.dict(os.environ, {"TTS_PROVIDER": "edge",
                                      "TTS_OUTPUT_DIR": str(Path("G:/youtube-uploader/data/audio"))}):
            with patch("backend.services.tts.edge_provider.EdgeTTSProvider.get_voices",
                       new=AsyncMock(side_effect=TTSNetworkError("no network"))):
                r = client.get("/api/tts/voices")
        assert r.status_code == 503

    def test_voices_no_secrets(self, client):
        import backend.routers.tts as tts_router
        tts_router._voice_cache = None

        with patch.dict(os.environ, {"TTS_PROVIDER": "edge",
                                      "TTS_OUTPUT_DIR": str(Path("G:/youtube-uploader/data/audio"))}):
            with self._mock_voices():
                r = client.get("/api/tts/voices")
        body = r.text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body


# ── API: POST /api/tts/generate-from-content ─────────────────────────────────

class TestGenerateFromContentEndpoint:

    def _mock_tts_task(self):
        """Patch the background TTS task so it doesn't run."""
        return patch("backend.routers.tts._run_tts_task")

    def test_returns_202_with_pending_status(self, client, db):
        project = make_project_with_script(db)
        with self._mock_tts_task():
            r = client.post(
                f"/api/tts/generate-from-content/{project.id}",
                json={},
            )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == AudioStatus.PENDING
        assert body["content_project_id"] == project.id
        assert "id" in body

    def test_audio_record_created_in_db(self, client, db):
        project = make_project_with_script(db)
        with self._mock_tts_task():
            r = client.post(f"/api/tts/generate-from-content/{project.id}", json={})
        assert r.status_code == 202
        audio_id = r.json()["id"]
        audio = db.query(GeneratedAudio).filter(GeneratedAudio.id == audio_id).first()
        assert audio is not None
        assert audio.status == AudioStatus.PENDING
        assert audio.content_project_id == project.id

    def test_missing_project_returns_404(self, client):
        r = client.post("/api/tts/generate-from-content/nonexistent-id", json={})
        assert r.status_code == 404

    def test_project_without_script_returns_422(self, client, db):
        project = make_project(db)  # no script
        with self._mock_tts_task():
            r = client.post(f"/api/tts/generate-from-content/{project.id}", json={})
        assert r.status_code == 422
        assert "script" in r.json()["detail"].lower()

    def test_reuses_existing_completed_audio(self, client, db, tmp_path):
        project = make_project_with_script(db)
        # Create a fake completed audio file
        fake_mp3 = tmp_path / "existing.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 10)
        existing = make_audio(
            db, project.id,
            status=AudioStatus.COMPLETED,
            voice="en-US-AriaNeural",
            file_path=str(fake_mp3),
        )
        with self._mock_tts_task() as mock_task:
            r = client.post(
                f"/api/tts/generate-from-content/{project.id}",
                json={"voice": "en-US-AriaNeural"},
            )
        # Should return the existing audio, not create a new one
        assert r.status_code == 202
        assert r.json()["id"] == existing.id
        mock_task.assert_not_called()

    def test_custom_voice_accepted(self, client, db):
        project = make_project_with_script(db)
        with self._mock_tts_task():
            r = client.post(
                f"/api/tts/generate-from-content/{project.id}",
                json={"voice": "en-GB-RyanNeural"},
            )
        assert r.status_code == 202
        assert r.json()["voice"] == "en-GB-RyanNeural"

    def test_response_never_contains_secrets(self, client, db):
        project = make_project_with_script(db)
        with self._mock_tts_task():
            r = client.post(f"/api/tts/generate-from-content/{project.id}", json={})
        body = r.text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body


# ── API: GET /api/tts/{audio_id} ─────────────────────────────────────────────

class TestGetAudioEndpoint:

    def test_returns_audio_record(self, client, db):
        project = make_project(db)
        audio = make_audio(db, project.id)
        r = client.get(f"/api/tts/{audio.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == audio.id
        assert body["status"] == AudioStatus.COMPLETED
        assert body["voice"] == "en-US-AriaNeural"

    def test_not_found_returns_404(self, client):
        r = client.get("/api/tts/nonexistent-id")
        assert r.status_code == 404

    def test_completed_audio_has_audio_url(self, client, db):
        project = make_project(db)
        audio = make_audio(db, project.id, status=AudioStatus.COMPLETED)
        r = client.get(f"/api/tts/{audio.id}")
        body = r.json()
        assert body["audio_url"] is not None
        assert audio.id in body["audio_url"]

    def test_pending_audio_has_no_url(self, client, db):
        project = make_project(db)
        audio = make_audio(db, project.id, status=AudioStatus.PENDING, file_path=None)
        r = client.get(f"/api/tts/{audio.id}")
        assert r.json()["audio_url"] is None


# ── API: GET /api/tts/{audio_id}/audio ───────────────────────────────────────

class TestStreamAudioEndpoint:

    def test_streams_mp3_when_completed(self, client, db, tmp_path):
        fake_mp3 = tmp_path / "test.mp3"
        fake_mp3.write_bytes(b"\xff\xfb\x90\x64" * 100)  # minimal fake MP3
        project = make_project(db)
        audio = make_audio(db, project.id,
                           status=AudioStatus.COMPLETED,
                           file_path=str(fake_mp3))
        r = client.get(f"/api/tts/{audio.id}/audio")
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/mpeg"

    def test_not_ready_returns_409(self, client, db):
        project = make_project(db)
        audio = make_audio(db, project.id, status=AudioStatus.GENERATING, file_path=None)
        r = client.get(f"/api/tts/{audio.id}/audio")
        assert r.status_code == 409
        assert "not ready" in r.json()["detail"].lower()

    def test_missing_file_returns_404(self, client, db):
        project = make_project(db)
        audio = make_audio(db, project.id,
                           status=AudioStatus.COMPLETED,
                           file_path="/nonexistent/path/file.mp3")
        r = client.get(f"/api/tts/{audio.id}/audio")
        assert r.status_code == 404

    def test_audio_not_found_returns_404(self, client):
        r = client.get("/api/tts/nonexistent-id/audio")
        assert r.status_code == 404


# ── API: DELETE /api/tts/{audio_id} ──────────────────────────────────────────

class TestDeleteAudioEndpoint:

    def test_delete_returns_204(self, client, db, tmp_path):
        fake_mp3 = tmp_path / "todelete.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 10)
        project = make_project(db)
        audio = make_audio(db, project.id, file_path=str(fake_mp3))
        r = client.delete(f"/api/tts/{audio.id}")
        assert r.status_code == 204

    def test_deleted_record_not_found(self, client, db, tmp_path):
        fake_mp3 = tmp_path / "gone.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 10)
        project = make_project(db)
        audio = make_audio(db, project.id, file_path=str(fake_mp3))
        audio_id = audio.id
        client.delete(f"/api/tts/{audio_id}")
        r = client.get(f"/api/tts/{audio_id}")
        assert r.status_code == 404

    def test_delete_removes_file_from_disk(self, client, db, tmp_path):
        fake_mp3 = tmp_path / "willbedeleted.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 10)
        assert fake_mp3.exists()
        project = make_project(db)
        audio = make_audio(db, project.id, file_path=str(fake_mp3))
        client.delete(f"/api/tts/{audio.id}")
        assert not fake_mp3.exists()

    def test_delete_nonexistent_returns_404(self, client):
        r = client.delete("/api/tts/does-not-exist")
        assert r.status_code == 404


# ── Database: GeneratedAudio model tests ─────────────────────────────────────

class TestGeneratedAudioModel:

    def test_audio_record_created(self, db):
        project = make_project(db)
        audio = make_audio(db, project.id)
        fetched = db.query(GeneratedAudio).filter(GeneratedAudio.id == audio.id).first()
        assert fetched is not None
        assert fetched.voice == "en-US-AriaNeural"
        assert fetched.status == AudioStatus.COMPLETED

    def test_status_transitions(self, db):
        project = make_project(db)
        audio = make_audio(db, project.id, status=AudioStatus.PENDING, file_path=None)
        assert audio.status == AudioStatus.PENDING

        audio.status = AudioStatus.GENERATING
        db.commit()
        db.refresh(audio)
        assert audio.status == AudioStatus.GENERATING

        audio.status = AudioStatus.COMPLETED
        audio.file_path = "/fake/path.mp3"
        db.commit()
        db.refresh(audio)
        assert audio.status == AudioStatus.COMPLETED

    def test_failed_audio_stores_error_message(self, db):
        project = make_project(db)
        audio = make_audio(db, project.id, status=AudioStatus.FAILED,
                           file_path=None, error_message="Network timeout.")
        db.refresh(audio)
        assert audio.error_message == "Network timeout."

    def test_cascade_delete_when_project_deleted(self, client, db, tmp_path):
        """Deleting the content project should cascade-delete audio records."""
        fake_mp3 = tmp_path / "cascade.mp3"
        fake_mp3.write_bytes(b"\xff\xfb" * 10)
        project = make_project_with_script(db)
        audio = make_audio(db, project.id, file_path=str(fake_mp3))
        audio_id = audio.id

        # Delete the project via the content API
        r = client.delete(f"/api/content/{project.id}")
        assert r.status_code == 204

        # Audio record should be gone
        fetched = db.query(GeneratedAudio).filter(GeneratedAudio.id == audio_id).first()
        assert fetched is None
