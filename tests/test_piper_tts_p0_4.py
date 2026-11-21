"""P0.4 — Piper provider + Edge→Piper→SAPI auto chain."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.tts.base import (
    TTSConfigError,
    TTSGenerationError,
    TTSNetworkError,
    TTSResult,
    TTSVoice,
)


def _result(path: str, provider: str, fmt: str = "wav") -> TTSResult:
    Path(path).write_bytes(b"RIFF....WAVEfmt ")
    return TTSResult(
        file_path=path,
        voice=f"{provider}-voice",
        format=fmt,
        file_size_bytes=16,
        duration_seconds=1.0,
        text_length=5,
    )


def test_factory_supports_piper(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_PROVIDER", "piper")
    monkeypatch.setenv("PIPER_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("TTS_OUTPUT_DIR", str(tmp_path))
    # Create fake model files so provider constructs cleanly
    onnx = tmp_path / "en_US-ljspeech-medium.onnx"
    onnx.write_bytes(b"fake")
    (tmp_path / "en_US-ljspeech-medium.onnx.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PIPER_VOICE", "en_US-ljspeech-medium")

    from backend.services.tts.factory import get_tts_provider
    from backend.services.tts.piper_provider import PiperTTSProvider

    p = get_tts_provider()
    assert isinstance(p, PiperTTSProvider)
    assert p.provider_name == "piper"


def test_piper_missing_model_raises_config(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPER_MODEL_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("TTS_OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("PIPER_MODEL_PATH", raising=False)
    from backend.services.tts.piper_provider import PiperTTSProvider

    provider = PiperTTSProvider()
    with pytest.raises(TTSConfigError, match="not found"):
        provider.resolve_model_path("en_US-ljspeech-medium")


def test_auto_chain_edge_to_piper_to_local(tmp_path):
    from backend.services.tts.manager import AutoTTSManager

    edge = MagicMock()
    edge.provider_name = "edge"
    edge.synthesize = AsyncMock(side_effect=TTSNetworkError("403"))

    piper = MagicMock()
    piper.provider_name = "piper"
    piper.synthesize = AsyncMock(
        return_value=_result(str(tmp_path / "p.wav"), "piper")
    )

    local = MagicMock()
    local.provider_name = "local"
    local.synthesize = AsyncMock()

    mgr = AutoTTSManager(providers=[edge, piper, local])
    result = asyncio.run(mgr.synthesize("Hello.", "en-US-AriaNeural", str(tmp_path / "o.mp3")))
    assert mgr.last_used_provider == "piper"
    assert result.format == "wav"
    local.synthesize.assert_not_called()


def test_auto_chain_falls_through_piper_config_to_local(tmp_path):
    from backend.services.tts.manager import AutoTTSManager

    edge = MagicMock()
    edge.provider_name = "edge"
    edge.synthesize = AsyncMock(side_effect=TTSNetworkError("403"))

    piper = MagicMock()
    piper.provider_name = "piper"
    piper.synthesize = AsyncMock(side_effect=TTSConfigError("model missing"))

    local = MagicMock()
    local.provider_name = "local"
    local.synthesize = AsyncMock(
        return_value=_result(str(tmp_path / "l.wav"), "local")
    )

    mgr = AutoTTSManager(providers=[edge, piper, local])
    result = asyncio.run(mgr.synthesize("Hello.", "en-US-AriaNeural", str(tmp_path / "o.mp3")))
    assert mgr.last_used_provider == "local"
    assert result.format == "wav"


def test_auto_chain_piper_generation_error_to_local(tmp_path):
    from backend.services.tts.manager import AutoTTSManager

    edge = MagicMock()
    edge.provider_name = "edge"
    edge.synthesize = AsyncMock(side_effect=TTSNetworkError("403"))
    piper = MagicMock()
    piper.provider_name = "piper"
    piper.synthesize = AsyncMock(side_effect=TTSGenerationError("onnx boom"))
    local = MagicMock()
    local.provider_name = "local"
    local.synthesize = AsyncMock(
        return_value=_result(str(tmp_path / "l.wav"), "local")
    )
    mgr = AutoTTSManager(providers=[edge, piper, local])
    asyncio.run(mgr.synthesize("Hi", "en-US-AriaNeural", str(tmp_path / "o.mp3")))
    assert mgr.last_used_provider == "local"


def test_adjust_path_for_piper(tmp_path):
    from backend.services.tts.manager import _adjust_path_for_provider

    piper = MagicMock()
    piper.provider_name = "piper"
    assert _adjust_path_for_provider(str(tmp_path / "a.mp3"), piper).endswith(".wav")


def test_get_voices_includes_piper_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPER_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("TTS_OUTPUT_DIR", str(tmp_path))
    onnx = tmp_path / "en_US-ljspeech-medium.onnx"
    onnx.write_bytes(b"x")
    (tmp_path / "en_US-ljspeech-medium.onnx.json").write_text("{}", encoding="utf-8")

    from backend.services.tts.piper_provider import PiperTTSProvider

    voices = asyncio.run(PiperTTSProvider().get_voices())
    assert any(v.name == "piper-en_US-ljspeech-medium" for v in voices)
