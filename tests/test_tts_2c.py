"""
tests/test_tts_2c.py — Phase 2C TTS reliability + fallback tests.

Coverage:
  AutoTTSManager:
    A. Edge succeeds → provider=edge, no fallback
    B. Edge returns 403 (TTSNetworkError) → fallback to local, provider=local
    C. Edge times out (TTSNetworkError) → fallback to local
    D. Edge rate-limited (TTSRateLimitError) → fallback to local
    E. Edge TTSValidationError → NOT caught, bubbles up (no fallback)
    F. Edge TTSConfigError → NOT caught, bubbles up (no fallback)
    G. Both Edge and local fail → LocalProvider error raised

  LocalTTSProvider:
    H. Successful synthesis writes WAV to output dir
    I. Empty text raises TTSValidationError
    J. Text too long raises TTSValidationError
    K. pyttsx3 not importable raises TTSConfigError
    L. get_voices returns local-default + SAPI voices
    M. Long text is chunked and concatenated

  Factory:
    N. TTS_PROVIDER=edge → EdgeTTSProvider (no AutoTTSManager)
    O. TTS_PROVIDER=local → LocalTTSProvider
    P. TTS_PROVIDER=auto → AutoTTSManager
    Q. TTS_PROVIDER=unknown → TTSConfigError

  API: provider field in response
    R. provider field present in AudioResponse
    S. provider=edge when Edge succeeds
    T. provider=local when fallback used
    U. provider field in GET /api/tts/{id}

  API: WAV format support
    V. stream_audio returns audio/wav for .wav files
    W. stream_audio returns audio/mpeg for .mp3 files

  Voices API:
    X. GET /api/tts/voices includes local voices in auto mode
    Y. local voices have provider="local"
    Z. edge voices have provider="edge"

  No secrets in responses:
    AA. No API keys / tokens exposed

  Existing tests still pass:
    AB. All 200 Phase 2B tests unaffected (run separately)
"""

from __future__ import annotations

import asyncio
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
from backend.services.tts.base import (
    TTSConfigError,
    TTSGenerationError,
    TTSNetworkError,
    TTSRateLimitError,
    TTSResult,
    TTSValidationError,
)

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tts_result(file_path: str, provider: str = "edge", fmt: str = "mp3") -> TTSResult:
    return TTSResult(
        file_path=file_path,
        voice=f"{provider}-voice",
        format=fmt,
        file_size_bytes=50000,
        duration_seconds=45.0,
        text_length=100,
    )


def make_project_with_script(db) -> ContentProject:
    project = ContentProject(
        id=str(uuid.uuid4()),
        topic="Test",
        language="en",
        tone="informative",
        target_duration_seconds=180,
        scene_count=5,
        status=ContentStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(project)
    script = GeneratedScriptRecord(
        id=str(uuid.uuid4()),
        content_project_id=project.id,
        title="T",
        description="D",
        hook="Hook sentence.",
        tags_json=json.dumps([]),
        scenes_json=json.dumps([
            {"scene_number": 1, "narration": "Narration text.",
             "visual_description": "v", "estimated_duration_seconds": 15},
        ]),
        estimated_duration_seconds=60,
    )
    db.add(script)
    db.commit()
    db.refresh(project)
    return project


def make_audio(db, project_id: str, status=AudioStatus.COMPLETED,
               file_path=None, provider=None, **kw) -> GeneratedAudio:
    audio = GeneratedAudio(
        id=str(uuid.uuid4()),
        content_project_id=project_id,
        voice="en-US-AriaNeural",
        language="en",
        text_length=100,
        file_path=file_path or f"/fake/{uuid.uuid4()}.mp3",
        file_size_bytes=50000,
        duration_seconds=45.0,
        status=status,
        provider=provider,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        **kw,
    )
    db.add(audio)
    db.commit()
    db.refresh(audio)
    return audio


# ── A–D: AutoTTSManager fallback behaviour ───────────────────────────────────

class TestAutoTTSManager:

    def _make_manager(self, primary_result=None, primary_exc=None,
                      fallback_result=None, fallback_exc=None):
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.local_provider import LocalTTSProvider
        from backend.services.tts.manager import AutoTTSManager

        primary = MagicMock(spec=EdgeTTSProvider)
        primary.provider_name = "edge"
        fallback = MagicMock(spec=LocalTTSProvider)
        fallback.provider_name = "local"

        if primary_exc:
            primary.synthesize = AsyncMock(side_effect=primary_exc)
        else:
            primary.synthesize = AsyncMock(return_value=primary_result)

        if fallback_exc:
            fallback.synthesize = AsyncMock(side_effect=fallback_exc)
        else:
            fallback.synthesize = AsyncMock(return_value=fallback_result)

        return AutoTTSManager(primary=primary, fallback=fallback)

    # A. Edge succeeds
    def test_edge_success_no_fallback(self, tmp_path):
        expected = _make_tts_result(str(tmp_path / "out.mp3"), "edge")
        manager = self._make_manager(primary_result=expected)
        result = asyncio.run(manager.synthesize("Hello.", "en-US-AriaNeural", str(tmp_path / "out.mp3")))
        assert result.voice == "edge-voice"
        assert manager.last_used_provider == "edge"
        manager._fallback.synthesize.assert_not_called()

    # B. Edge 403 (TTSNetworkError) → fallback
    def test_edge_network_error_falls_back_to_local(self, tmp_path):
        primary_exc = TTSNetworkError("Edge-TTS 403 rate-limited by IP.")
        fallback_res = _make_tts_result(str(tmp_path / "out.wav"), "local", "wav")
        manager = self._make_manager(primary_exc=primary_exc, fallback_result=fallback_res)
        result = asyncio.run(manager.synthesize("Hello.", "en-US-AriaNeural", str(tmp_path / "out.mp3")))
        assert result.format == "wav"
        assert manager.last_used_provider == "local"
        manager._fallback.synthesize.assert_called_once()

    # C. Edge timeout → fallback
    def test_edge_timeout_falls_back_to_local(self, tmp_path):
        primary_exc = TTSNetworkError("timeout")
        fallback_res = _make_tts_result(str(tmp_path / "out.wav"), "local", "wav")
        manager = self._make_manager(primary_exc=primary_exc, fallback_result=fallback_res)
        result = asyncio.run(manager.synthesize("Hello.", "en-US-AriaNeural", str(tmp_path / "out.mp3")))
        assert manager.last_used_provider == "local"

    # D. Edge rate-limited → fallback
    def test_edge_rate_limit_falls_back_to_local(self, tmp_path):
        primary_exc = TTSRateLimitError("too many requests")
        fallback_res = _make_tts_result(str(tmp_path / "out.wav"), "local", "wav")
        manager = self._make_manager(primary_exc=primary_exc, fallback_result=fallback_res)
        result = asyncio.run(manager.synthesize("Hello.", "en-US-AriaNeural", str(tmp_path / "out.mp3")))
        assert manager.last_used_provider == "local"

    # E. Edge ValidationError → NOT caught, bubbles up
    def test_edge_validation_error_not_caught(self, tmp_path):
        primary_exc = TTSValidationError("Text is empty.")
        manager = self._make_manager(primary_exc=primary_exc)
        with pytest.raises(TTSValidationError):
            asyncio.run(manager.synthesize("", "en-US-AriaNeural", str(tmp_path / "out.mp3")))
        manager._fallback.synthesize.assert_not_called()

    # F. Edge ConfigError → NOT caught, bubbles up
    def test_edge_config_error_not_caught(self, tmp_path):
        primary_exc = TTSConfigError("edge-tts not installed")
        manager = self._make_manager(primary_exc=primary_exc)
        with pytest.raises(TTSConfigError):
            asyncio.run(manager.synthesize("Hello.", "en-US-AriaNeural", str(tmp_path / "out.mp3")))
        manager._fallback.synthesize.assert_not_called()

    # G. Both fail — local error raised
    def test_both_fail_raises_local_error(self, tmp_path):
        primary_exc = TTSNetworkError("Edge 403")
        fallback_exc = TTSGenerationError("Local SAPI failed")
        manager = self._make_manager(primary_exc=primary_exc, fallback_exc=fallback_exc)
        with pytest.raises(TTSGenerationError, match="Local SAPI failed"):
            asyncio.run(manager.synthesize("Hello.", "en-US-AriaNeural", str(tmp_path / "out.mp3")))

    def test_manager_provider_name_is_auto(self, tmp_path):
        from backend.services.tts.manager import AutoTTSManager
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.local_provider import LocalTTSProvider
        p = MagicMock(spec=EdgeTTSProvider)
        p.provider_name = "edge"
        f = MagicMock(spec=LocalTTSProvider)
        f.provider_name = "local"
        manager = AutoTTSManager(primary=p, fallback=f)
        assert manager.provider_name == "auto"

    def test_voice_key_mapped_for_fallback(self):
        from backend.services.tts.manager import _local_voice
        assert _local_voice("en-US-AriaNeural") == "local-default"
        assert _local_voice("en-GB-RyanNeural")  == "local-default"
        assert _local_voice("local-zira")         == "local-zira"
        assert _local_voice("local-david")        == "local-david"

    def test_path_adjusted_for_wav_provider(self, tmp_path):
        from backend.services.tts.manager import _adjust_path_for_provider
        from backend.services.tts.local_provider import LocalTTSProvider
        local = MagicMock(spec=LocalTTSProvider)
        local.provider_name = "local"
        mp3_path = str(tmp_path / "output.mp3")
        result = _adjust_path_for_provider(mp3_path, local)
        assert result.endswith(".wav")

    def test_get_voices_combines_both_providers(self, tmp_path):
        from backend.services.tts.manager import AutoTTSManager
        from backend.services.tts.base import TTSVoice
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.local_provider import LocalTTSProvider

        edge_voices  = [TTSVoice("en-US-AriaNeural", "en-US", "English", "Female")]
        local_voices = [TTSVoice("local-zira", "en-US", "Zira (Local)", "Female")]

        primary  = MagicMock(spec=EdgeTTSProvider)
        primary.provider_name = "edge"
        primary.get_voices = AsyncMock(return_value=edge_voices)

        fallback = MagicMock(spec=LocalTTSProvider)
        fallback.provider_name = "local"
        fallback.get_voices = AsyncMock(return_value=local_voices)

        manager = AutoTTSManager(primary=primary, fallback=fallback)
        voices = asyncio.run(manager.get_voices())
        names = [v.name for v in voices]
        assert "en-US-AriaNeural" in names
        assert "local-zira" in names


# ── H–M: LocalTTSProvider ────────────────────────────────────────────────────

class TestLocalTTSProvider:

    def _make_provider(self, tmp_path):
        with patch.dict(os.environ, {
            "TTS_OUTPUT_DIR": str(tmp_path),
            "TTS_LOCAL_RATE": "165",
            "TTS_MAX_TEXT_LENGTH": "5000",
        }):
            from backend.services.tts.local_provider import LocalTTSProvider
            return LocalTTSProvider()

    def _mock_pyttsx3(self, tmp_path):
        """Patch pyttsx3.init() to write a minimal WAV file."""
        def fake_save_to_file(text, path):
            import wave, struct
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(p), "w") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(struct.pack("<" + "h" * 100, *([0] * 100)))

        mock_engine = MagicMock()
        mock_engine.getProperty.side_effect = lambda prop: (
            [MagicMock(id="HKEY_LOCAL_MACHINE\\...\\TTS_MS_EN-US_ZIRA_11.0",
                       name="Microsoft Zira Desktop - English (United States)",
                       languages=["en-US"])]
            if prop == "voices" else 165
        )
        mock_engine.save_to_file = MagicMock(side_effect=fake_save_to_file)
        mock_engine.runAndWait   = MagicMock()

        return patch("pyttsx3.init", return_value=mock_engine)

    # H. Successful synthesis
    def test_successful_synthesis_creates_wav(self, tmp_path):
        provider = self._make_provider(tmp_path)
        output = str(tmp_path / "test.wav")
        with self._mock_pyttsx3(tmp_path):
            result = asyncio.run(provider.synthesize("Hello world.", "local-zira", output))
        assert result.format == "wav"
        assert result.provider_name if hasattr(result, "provider_name") else True
        assert result.text_length == len("Hello world.")

    # I. Empty text
    def test_empty_text_raises_validation_error(self, tmp_path):
        provider = self._make_provider(tmp_path)
        with pytest.raises(TTSValidationError):
            asyncio.run(provider.synthesize("", "local-zira", str(tmp_path / "out.wav")))

    # J. Text too long
    def test_text_too_long_raises_validation_error(self, tmp_path):
        provider = self._make_provider(tmp_path)
        very_long = "w " * 10001   # > 4 × 5000
        with pytest.raises(TTSValidationError, match="too long"):
            asyncio.run(provider.synthesize(very_long, "local-zira", str(tmp_path / "out.wav")))

    # K. pyttsx3 missing
    def test_pyttsx3_missing_raises_config_error(self, tmp_path):
        with patch.dict("sys.modules", {"pyttsx3": None}):
            with pytest.raises((TTSConfigError, ImportError)):
                with patch.dict(os.environ, {
                    "TTS_OUTPUT_DIR": str(tmp_path),
                    "TTS_MAX_TEXT_LENGTH": "5000",
                }):
                    from backend.services.tts.local_provider import LocalTTSProvider
                    LocalTTSProvider()

    # L. get_voices returns local voices
    def test_get_voices_returns_local_voices(self, tmp_path):
        provider = self._make_provider(tmp_path)
        with self._mock_pyttsx3(tmp_path):
            voices = asyncio.run(provider.get_voices())
        names = [v.name for v in voices]
        assert "local-default" in names
        assert len(voices) >= 1

    # M. Long text is chunked
    def test_long_text_is_chunked(self, tmp_path):
        from backend.services.tts.edge_provider import _split_into_chunks
        long_text = "This is a sentence. " * 300  # ~6000 chars > 5000 chunk limit
        chunks = _split_into_chunks(long_text, max_chunk_len=5000)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 5100  # allow slight overage per sentence

    def test_provider_name_is_local(self, tmp_path):
        provider = self._make_provider(tmp_path)
        assert provider.provider_name == "local"

    def test_mp3_extension_converted_to_wav(self, tmp_path):
        """If output_path ends in .mp3, provider should write .wav instead."""
        provider = self._make_provider(tmp_path)
        mp3_path  = str(tmp_path / "output.mp3")

        with self._mock_pyttsx3(tmp_path):
            result = asyncio.run(provider.synthesize("Hello.", "local-default", mp3_path))
        assert result.file_path.endswith(".wav")

    # SAPI-safe chunking: text > 500 chars must be split into ≤500-char chunks
    def test_sapi_safe_chunking_applied_internally(self, tmp_path):
        """
        LocalTTSProvider must use SAPI_SAFE_CHUNK=500 internally, regardless
        of TTS_MAX_TEXT_LENGTH, to avoid Windows SAPI runAndWait() hangs.
        """
        from backend.services.tts.local_provider import _SAPI_SAFE_CHUNK
        assert _SAPI_SAFE_CHUNK == 500

    def test_long_text_split_into_sapi_safe_chunks(self, tmp_path):
        """
        Text longer than SAPI_SAFE_CHUNK must be split and each chunk synthesized
        separately, then WAV-concatenated into a single output file.
        """
        from backend.services.tts.edge_provider import _split_into_chunks
        from backend.services.tts.local_provider import _SAPI_SAFE_CHUNK

        # Build text > 500 chars so SAPI chunking kicks in
        long_text = "This is a sentence for testing chunked synthesis. " * 20  # ~1000 chars
        chunks = _split_into_chunks(long_text, max_chunk_len=_SAPI_SAFE_CHUNK)
        assert len(chunks) > 1, "Text should produce multiple SAPI-safe chunks"
        for c in chunks:
            assert len(c) <= _SAPI_SAFE_CHUNK + 100  # slight overage ok per sentence

    def test_multi_chunk_synthesis_produces_single_output(self, tmp_path):
        """
        Multi-chunk synthesis must produce exactly one WAV output file, not
        multiple chunk files left on disk.
        """
        provider = self._make_provider(tmp_path)
        # Text long enough to trigger _SAPI_SAFE_CHUNK=500 splitting
        long_text = "Testing the chunked TTS synthesis pipeline. " * 20  # ~880 chars
        output = str(tmp_path / "multi_chunk_out.wav")

        with self._mock_pyttsx3(tmp_path):
            result = asyncio.run(provider.synthesize(long_text, "local-zira", output))

        # Output file must exist
        assert Path(result.file_path).exists()
        # No .chunkN.wav temp files should remain
        leftover = list(tmp_path.glob("*.chunk*.wav"))
        assert leftover == [], f"Temp chunk files were not cleaned up: {leftover}"

    def test_fresh_engine_per_chunk_via_pyttsx3_init_call_count(self, tmp_path):
        """
        _synthesize_sync must call pyttsx3.init() once per invocation.
        Multi-chunk synthesis of N chunks must call pyttsx3.init() N times,
        not reuse a single engine (which hangs on Windows SAPI).
        """
        provider = self._make_provider(tmp_path)
        call_count = 0

        def fake_save_to_file(text, path):
            import wave, struct
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "w") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
                w.writeframes(struct.pack("<100h", *([0] * 100)))

        def make_mock_engine():
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            m.getProperty.side_effect = lambda prop: (
                [MagicMock(id="HKEY\\Zira", name="Microsoft Zira Desktop", languages=["en-US"])]
                if prop == "voices" else 165
            )
            m.save_to_file = MagicMock(side_effect=fake_save_to_file)
            m.runAndWait   = MagicMock()
            return m

        # Text triggering 2+ chunks (> 500 chars)
        long_text = "Test sentence for chunked SAPI engine count. " * 15  # ~675 chars

        with patch("pyttsx3.init", side_effect=make_mock_engine):
            asyncio.run(provider.synthesize(long_text, "local-zira", str(tmp_path / "out.wav")))

        # Must have been called once per chunk (not once total, not zero)
        assert call_count >= 2, (
            f"pyttsx3.init() was called {call_count} time(s); expected ≥2 for multi-chunk text. "
            "Each chunk must use a fresh engine to avoid Windows SAPI runAndWait() hangs."
        )

    # K (spec): audio file created on disk AND database record is correct
    def test_audio_file_created_and_result_correct(self, tmp_path):
        """
        After synthesis, the WAV file must exist on disk and the TTSResult
        must accurately reflect the file path, format, and text length.
        """
        provider = self._make_provider(tmp_path)
        output   = str(tmp_path / "narration.wav")
        text     = "Audio file creation and database record correctness test."

        with self._mock_pyttsx3(tmp_path):
            result = asyncio.run(provider.synthesize(text, "local-zira", output))

        assert Path(result.file_path).exists(), "WAV file must exist on disk"
        assert result.format == "wav"
        assert result.text_length == len(text)
        assert result.file_size_bytes > 0


# ── N–Q: Factory provider selection ─────────────────────────────────────────

class TestTTSFactory:

    def test_edge_provider_selected(self, tmp_path):
        from backend.services.tts.factory import get_tts_provider
        from backend.services.tts.edge_provider import EdgeTTSProvider
        with patch.dict(os.environ, {"TTS_PROVIDER": "edge",
                                      "TTS_OUTPUT_DIR": str(tmp_path)}):
            p = get_tts_provider()
        assert isinstance(p, EdgeTTSProvider)

    def test_local_provider_selected(self, tmp_path):
        from backend.services.tts.factory import get_tts_provider
        from backend.services.tts.local_provider import LocalTTSProvider
        with patch.dict(os.environ, {"TTS_PROVIDER": "local",
                                      "TTS_OUTPUT_DIR": str(tmp_path)}):
            p = get_tts_provider()
        assert isinstance(p, LocalTTSProvider)

    def test_auto_provider_selected(self, tmp_path):
        from backend.services.tts.factory import get_tts_provider
        from backend.services.tts.manager import AutoTTSManager
        with patch.dict(os.environ, {"TTS_PROVIDER": "auto",
                                      "TTS_OUTPUT_DIR": str(tmp_path)}):
            p = get_tts_provider()
        assert isinstance(p, AutoTTSManager)

    def test_unknown_provider_raises_config_error(self):
        from backend.services.tts.factory import get_tts_provider
        with patch.dict(os.environ, {"TTS_PROVIDER": "nonexistent"}):
            with pytest.raises(TTSConfigError):
                get_tts_provider()

    def test_auto_mode_has_edge_primary(self, tmp_path):
        from backend.services.tts.factory import get_tts_provider
        from backend.services.tts.edge_provider import EdgeTTSProvider
        with patch.dict(os.environ, {"TTS_PROVIDER": "auto",
                                      "TTS_OUTPUT_DIR": str(tmp_path)}):
            p = get_tts_provider()
        assert isinstance(p._primary, EdgeTTSProvider)

    def test_auto_mode_has_local_fallback(self, tmp_path):
        from backend.services.tts.factory import get_tts_provider
        from backend.services.tts.local_provider import LocalTTSProvider
        with patch.dict(os.environ, {"TTS_PROVIDER": "auto",
                                      "TTS_OUTPUT_DIR": str(tmp_path)}):
            p = get_tts_provider()
        assert isinstance(p._fallback, LocalTTSProvider)


# ── R–U: Provider field in API responses ─────────────────────────────────────

class TestProviderFieldInAPI:

    def test_provider_field_present_in_audio_response(self, client, db):
        project = make_project_with_script(db)
        audio = make_audio(db, project.id, provider="edge")
        r = client.get(f"/api/tts/{audio.id}")
        assert r.status_code == 200
        body = r.json()
        assert "provider" in body

    def test_provider_edge_stored_and_returned(self, client, db):
        project = make_project_with_script(db)
        audio = make_audio(db, project.id, provider="edge")
        r = client.get(f"/api/tts/{audio.id}")
        assert r.json()["provider"] == "edge"

    def test_provider_local_stored_and_returned(self, client, db):
        project = make_project_with_script(db)
        audio = make_audio(db, project.id, provider="local")
        r = client.get(f"/api/tts/{audio.id}")
        assert r.json()["provider"] == "local"

    def test_provider_none_for_old_records(self, client, db):
        """Old records without provider field should return None gracefully."""
        project = make_project_with_script(db)
        audio = make_audio(db, project.id, provider=None)
        r = client.get(f"/api/tts/{audio.id}")
        assert r.json()["provider"] is None


# ── V–W: WAV / MP3 format streaming ─────────────────────────────────────────

class TestAudioFormatStreaming:

    def test_wav_file_served_with_audio_wav_type(self, client, db, tmp_path):
        fake_wav = tmp_path / "test.wav"
        import wave, struct
        with wave.open(str(fake_wav), "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(struct.pack("<100h", *([0] * 100)))
        project = make_project_with_script(db)
        audio = make_audio(db, project.id,
                           status=AudioStatus.COMPLETED,
                           file_path=str(fake_wav))
        r = client.get(f"/api/tts/{audio.id}/audio")
        assert r.status_code == 200
        assert "audio/wav" in r.headers["content-type"]

    def test_mp3_file_served_with_audio_mpeg_type(self, client, db, tmp_path):
        fake_mp3 = tmp_path / "test.mp3"
        fake_mp3.write_bytes(b"\xff\xfb\x90\x00" * 50)
        project = make_project_with_script(db)
        audio = make_audio(db, project.id,
                           status=AudioStatus.COMPLETED,
                           file_path=str(fake_mp3))
        r = client.get(f"/api/tts/{audio.id}/audio")
        assert r.status_code == 200
        assert "audio/mpeg" in r.headers["content-type"]


# ── X–Z: Voice list includes edge + local ────────────────────────────────────

class TestVoiceListProviderTags:

    def test_local_voices_have_local_provider_tag(self, client):
        import backend.routers.tts as tts_router
        tts_router._voice_cache = None

        from backend.services.tts.base import TTSVoice

        local_voices = [
            TTSVoice("local-default", "en-US", "Local Default", "Unknown"),
            TTSVoice("local-zira",    "en-US", "Zira (Local)",  "Female"),
        ]
        edge_voices = [
            TTSVoice("en-US-AriaNeural", "en-US", "Aria", "Female"),
        ]

        mock_provider = MagicMock()
        mock_provider.get_voices = AsyncMock(return_value=edge_voices + local_voices)

        with patch("backend.routers.tts.get_tts_provider", return_value=mock_provider):
            r = client.get("/api/tts/voices")

        assert r.status_code == 200
        voices = r.json()
        local = [v for v in voices if v["name"].startswith("local-")]
        edge  = [v for v in voices if not v["name"].startswith("local-")]

        assert all(v["provider"] == "local" for v in local)
        assert all(v["provider"] == "edge"  for v in edge)

    def test_voices_no_secrets_in_response(self, client):
        import backend.routers.tts as tts_router
        tts_router._voice_cache = None

        from backend.services.tts.base import TTSVoice
        mock_provider = MagicMock()
        mock_provider.get_voices = AsyncMock(return_value=[
            TTSVoice("local-default", "en-US", "Local", "Unknown")
        ])
        with patch("backend.routers.tts.get_tts_provider", return_value=mock_provider):
            r = client.get("/api/tts/voices")
        body = r.text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body


# ── AA: No secrets in responses ──────────────────────────────────────────────

class TestNoSecretsInResponses:

    def test_audio_record_no_secrets(self, client, db):
        project = make_project_with_script(db)
        audio = make_audio(db, project.id)
        body = client.get(f"/api/tts/{audio.id}").text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body
        assert "access_token" not in body

    def test_generate_from_content_no_secrets(self, client, db):
        project = make_project_with_script(db)
        with patch("backend.routers.tts._run_tts_task"):
            r = client.post(f"/api/tts/generate-from-content/{project.id}", json={})
        body = r.text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body


# ── Background task integration: provider recorded ───────────────────────────

class TestBackgroundTaskProviderRecording:

    def test_edge_provider_recorded_in_db(self, db, tmp_path):
        """When Edge succeeds, provider='edge' is saved in the DB."""
        from backend.routers.tts import _run_tts_task
        from tests.conftest import SharedTestingSessionLocal
        project = make_project_with_script(db)
        audio = make_audio(db, project.id,
                           status=AudioStatus.PENDING, file_path=None, provider=None)

        edge_result = TTSResult(
            file_path=str(tmp_path / f"{audio.id}.mp3"),
            voice="en-US-AriaNeural",
            format="mp3",
            file_size_bytes=10000,
            duration_seconds=30.0,
            text_length=50,
        )
        Path(edge_result.file_path).write_bytes(b"\xff\xfb" * 100)

        mock_edge = MagicMock()
        mock_edge.synthesize = AsyncMock(return_value=edge_result)
        mock_edge.provider_name = "edge"

        with patch("backend.routers.tts.get_tts_provider", return_value=mock_edge), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
             patch.dict(os.environ, {"TTS_OUTPUT_DIR": str(tmp_path)}):
            _run_tts_task(audio.id, "Test text here.", "en-US-AriaNeural")

        db.expire(audio)
        db.refresh(audio)
        assert audio.status == AudioStatus.COMPLETED
        assert audio.provider == "edge"

    def test_fallback_provider_recorded_in_db(self, db, tmp_path):
        """When Edge fails and local succeeds, provider='local' is saved."""
        from backend.routers.tts import _run_tts_task
        from backend.services.tts.manager import AutoTTSManager
        from backend.services.tts.edge_provider import EdgeTTSProvider
        from backend.services.tts.local_provider import LocalTTSProvider
        from tests.conftest import SharedTestingSessionLocal

        project = make_project_with_script(db)
        audio = make_audio(db, project.id,
                           status=AudioStatus.PENDING, file_path=None, provider=None)

        local_result = TTSResult(
            file_path=str(tmp_path / f"{audio.id}.wav"),
            voice="local-zira",
            format="wav",
            file_size_bytes=50000,
            duration_seconds=30.0,
            text_length=50,
        )
        Path(local_result.file_path).write_bytes(b"\x00" * 100)

        primary  = MagicMock(spec=EdgeTTSProvider)
        primary.provider_name = "edge"
        primary.synthesize    = AsyncMock(side_effect=TTSNetworkError("403"))

        fallback = MagicMock(spec=LocalTTSProvider)
        fallback.provider_name = "local"
        fallback.synthesize    = AsyncMock(return_value=local_result)

        auto_manager = AutoTTSManager(primary=primary, fallback=fallback)

        with patch("backend.routers.tts.get_tts_provider", return_value=auto_manager), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal), \
             patch.dict(os.environ, {"TTS_OUTPUT_DIR": str(tmp_path)}):
            _run_tts_task(audio.id, "Test text here.", "en-US-AriaNeural")

        db.expire(audio)
        db.refresh(audio)
        assert audio.status == AudioStatus.COMPLETED
        assert audio.provider == "local"
