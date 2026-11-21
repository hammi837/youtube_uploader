"""
backend/services/tts/piper_provider.py — Local neural TTS via Piper (CPU).

Uses the maintained `piper-tts` package (OHF-Voice/piper1-gpl) with ONNX
Runtime on CPU. No CUDA required. No paid API.

Models are NOT downloaded automatically. Place a voice under PIPER_MODEL_DIR
(or set PIPER_MODEL_PATH to an .onnx file) before use.

Default recommended voice (when present):
    en_US-ljspeech-medium  — public-domain LJSpeech female, clear narration,
                             22.05 kHz, trained from scratch (no lessac chain).

Configuration:
    PIPER_MODEL_DIR       — directory of .onnx + .onnx.json voices
                            (default: G:\\youtube-uploader\\data\\models\\piper)
    PIPER_MODEL_PATH      — absolute path to a specific .onnx (overrides dir/name)
    PIPER_VOICE           — voice stem, e.g. en_US-ljspeech-medium
    PIPER_LENGTH_SCALE    — speaking-rate multiplier (>1 slower, <1 faster; default 1.0)
    PIPER_NOISE_SCALE     — audio variation (default 0.667)
    PIPER_NOISE_W_SCALE   — phoneme width variation (default 0.8)
    TTS_OUTPUT_DIR        — where WAV files are written
    TTS_MAX_TEXT_LENGTH   — max chars accepted (default 8000)
"""

from __future__ import annotations

import asyncio
import logging
import os
import wave
from pathlib import Path
from typing import Optional

from backend.services.tts.base import (
    TTSConfigError,
    TTSGenerationError,
    TTSProvider,
    TTSResult,
    TTSValidationError,
    TTSVoice,
)
from backend.services.tts.edge_provider import (
    _clean_text_for_tts,
    _estimate_duration,
    _validate_text,
)

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = r"G:\youtube-uploader\data\audio"
_DEFAULT_MODEL_DIR = r"G:\youtube-uploader\data\models\piper"
_DEFAULT_VOICE = "en_US-ljspeech-medium"
_DEFAULT_MAX_TEXT = 8000


class PiperTTSProvider(TTSProvider):
    """
    Local neural TTS using Piper + ONNX Runtime (CPU).

    Raises TTSConfigError when piper-tts is missing or the voice model is absent.
    """

    def __init__(self) -> None:
        self._output_dir = Path(os.getenv("TTS_OUTPUT_DIR", _DEFAULT_OUTPUT_DIR))
        self._model_dir = Path(os.getenv("PIPER_MODEL_DIR", _DEFAULT_MODEL_DIR))
        self._default_voice = os.getenv("PIPER_VOICE", _DEFAULT_VOICE).strip()
        self._max_text = int(os.getenv("TTS_MAX_TEXT_LENGTH", str(_DEFAULT_MAX_TEXT)))
        self._length_scale = float(os.getenv("PIPER_LENGTH_SCALE", "1.0"))
        self._noise_scale = float(os.getenv("PIPER_NOISE_SCALE", "0.667"))
        self._noise_w_scale = float(os.getenv("PIPER_NOISE_W_SCALE", "0.8"))
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._voice_cache: dict[str, object] = {}

        try:
            import piper  # noqa: F401
        except ImportError as exc:
            raise TTSConfigError(
                "piper-tts is not installed. Run: pip install piper-tts. "
                "Then download a voice into PIPER_MODEL_DIR (see docs/PIPER_TTS.md)."
            ) from exc

        logger.info(
            "PiperTTSProvider initialized: model_dir=%s default_voice=%s length_scale=%.2f",
            self._model_dir, self._default_voice, self._length_scale,
        )

    @property
    def provider_name(self) -> str:
        return "piper"

    def is_available(self) -> bool:
        """True if the default (or configured) voice model files exist."""
        try:
            self.resolve_model_path(self._default_voice)
            return True
        except TTSConfigError:
            return False

    def resolve_model_path(self, voice: str) -> Path:
        """
        Resolve an .onnx model path for a voice key.

        Accepts:
          - Absolute/relative .onnx path
          - Voice stem like en_US-ljspeech-medium
          - piper-* aliases mapped to PIPER_VOICE
          - Edge/local voice names → fall back to PIPER_VOICE
        """
        explicit = os.getenv("PIPER_MODEL_PATH", "").strip()
        if explicit:
            p = Path(explicit)
            if not p.is_absolute():
                p = self._model_dir / p
            if p.suffix.lower() != ".onnx":
                p = p if p.suffix == "" else p
                if p.suffix == "":
                    p = Path(str(p) + ".onnx")
            cfg = Path(str(p) + ".json")
            if p.exists() and cfg.exists():
                return p
            raise TTSConfigError(
                f"PIPER_MODEL_PATH set but model missing or incomplete: {p}. "
                "Need both .onnx and .onnx.json."
            )

        name = (voice or "").strip() or self._default_voice
        if name.startswith("piper-"):
            name = name[len("piper-"):] or self._default_voice
        # Edge / SAPI names are not Piper voices — use configured default
        if (
            name.startswith("en-")
            or name.startswith("local-")
            or "Neural" in name
        ):
            name = self._default_voice

        if name.lower().endswith(".onnx"):
            candidate = Path(name)
            if not candidate.is_absolute():
                candidate = self._model_dir / candidate.name
        else:
            candidate = self._model_dir / f"{name}.onnx"

        cfg = Path(str(candidate) + ".json")
        if not candidate.exists() or not cfg.exists():
            raise TTSConfigError(
                f"Piper voice model not found: {candidate.name}. "
                f"Expected files:\n  {candidate}\n  {cfg}\n"
                f"Download manually into {self._model_dir} "
                f"(see docs/PIPER_TTS.md). Automatic download is disabled."
            )
        return candidate

    def _load_voice(self, model_path: Path):
        key = str(model_path.resolve())
        if key in self._voice_cache:
            return self._voice_cache[key]
        from piper import PiperVoice

        logger.info("Loading Piper voice model: %s (CPU)", model_path.name)
        voice = PiperVoice.load(str(model_path), use_cuda=False)
        self._voice_cache[key] = voice
        return voice

    def _synthesize_sync(self, text: str, voice_name: str, output_path: str) -> TTSResult:
        model_path = self.resolve_model_path(voice_name)
        piper_voice = self._load_voice(model_path)

        out = Path(output_path)
        if out.suffix.lower() == ".mp3":
            out = out.with_suffix(".wav")
        if not out.is_absolute():
            out = self._output_dir / out.name
        out.parent.mkdir(parents=True, exist_ok=True)

        cleaned = _clean_text_for_tts(text)
        if not cleaned.strip():
            raise TTSValidationError("Text is empty after cleaning.")

        syn_config = None
        try:
            from piper.config import SynthesisConfig
            syn_config = SynthesisConfig(
                volume=1.0,
                length_scale=self._length_scale,
                noise_scale=self._noise_scale,
                noise_w_scale=self._noise_w_scale,
                normalize_audio=True,
            )
        except Exception:
            syn_config = None

        try:
            with wave.open(str(out), "wb") as wav_file:
                if syn_config is not None:
                    piper_voice.synthesize_wav(cleaned, wav_file, syn_config=syn_config)
                else:
                    piper_voice.synthesize_wav(cleaned, wav_file)
        except Exception as exc:
            raise TTSGenerationError(
                f"Piper synthesis failed ({type(exc).__name__}): {exc}"
            ) from exc

        if not out.exists() or out.stat().st_size < 44:
            raise TTSGenerationError(f"Piper produced empty/invalid WAV: {out}")

        duration = _wav_duration(out)
        if duration is None:
            duration = _estimate_duration(cleaned)

        logger.info(
            "Piper TTS completed: %s size=%d bytes duration=%.1fs model=%s",
            out.name, out.stat().st_size, duration or 0.0, model_path.name,
        )
        return TTSResult(
            file_path=str(out),
            voice=model_path.stem,
            format="wav",
            file_size_bytes=out.stat().st_size,
            duration_seconds=duration,
            text_length=len(cleaned),
        )

    async def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
    ) -> TTSResult:
        _validate_text(text, self._max_text)
        # Resolve early so missing-model errors are TTSConfigError before work starts
        self.resolve_model_path(voice or self._default_voice)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._synthesize_sync,
            text,
            voice or self._default_voice,
            output_path,
        )

    async def get_voices(self, language: Optional[str] = None) -> list[TTSVoice]:
        voices: list[TTSVoice] = []
        for onnx in sorted(self._model_dir.glob("*.onnx")):
            cfg = Path(str(onnx) + ".json")
            if not cfg.exists():
                continue
            stem = onnx.stem  # e.g. en_US-ljspeech-medium
            locale = stem.split("-")[0] if "-" in stem else "en_US"
            lang_code = locale.split("_")[0].lower()
            if language and not lang_code.startswith(language.lower()[:2]):
                continue
            gender = "Unknown"
            lower = stem.lower()
            if any(x in lower for x in ("ljspeech", "kathleen", "amy", "jenny", "lessac", "cori", "alba")):
                gender = "Female"
            elif any(x in lower for x in ("joe", "mike", "ryan", "norman", "john", "bryce")):
                gender = "Male"
            voices.append(TTSVoice(
                name=f"piper-{stem}",
                locale=locale.replace("_", "-"),
                language=lang_code,
                gender=gender,
            ))
        if not voices:
            # Advertise configured default even if missing (UI can show setup needed)
            voices.append(TTSVoice(
                name=f"piper-{self._default_voice}",
                locale="en-US",
                language="en",
                gender="Female",
            ))
        return voices


def _wav_duration(path: Path) -> Optional[float]:
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate <= 0:
                return None
            return round(frames / float(rate), 2)
    except Exception:
        return None
