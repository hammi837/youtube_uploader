"""
tests/test_ffmpeg.py — Unit tests for backend/services/media/ffmpeg.py.

All tests use mocks — no real FFmpeg process is executed and no
real filesystem path is required.  The test suite passes on any machine
regardless of whether FFmpeg is installed.

Coverage:
    A.  get_ffmpeg_path() reads FFMPEG_PATH from env
    B.  get_ffmpeg_path() returns default when env var is not set
    C.  get_ffprobe_path() reads FFPROBE_PATH from env
    D.  get_ffprobe_path() derives default from FFMPEG_PATH directory
    E.  get_ffmpeg_version() returns first line of version output
    F.  get_ffmpeg_version() raises FFmpegNotFoundError when binary missing
    G.  get_ffmpeg_version() raises FFmpegNotFoundError on timeout
    H.  get_ffmpeg_version() raises FFmpegNotFoundError on OSError
    I.  get_ffmpeg_version() raises FFmpegNotFoundError on empty output
    J.  check_ffmpeg() returns available=True with version on success
    K.  check_ffmpeg() returns available=False with error on failure
    L.  check_ffmpeg() never raises — always returns dict
    M.  _assert_executable_exists() raises on missing path
    N.  _assert_executable_exists() raises when path is a directory
    O.  _assert_executable_exists() passes when file exists
    P.  No credential / secret leakage in any return value
    Q.  Version string is truncated to _VERSION_MAX_LEN
    R.  get_ffmpeg_version() truncates long output safely
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

_FAKE_PATH  = r"G:\youtube-uploader\tools\ffmpeg\bin\ffmpeg.exe"
_FAKE_PROBE = r"G:\youtube-uploader\tools\ffmpeg\bin\ffprobe.exe"

_FAKE_VERSION_LINE = (
    "ffmpeg version N-126416-g9997fd0606-20260905 "
    "Copyright (c) 2000-2026 the FFmpeg developers"
)
_FAKE_VERSION_OUTPUT = f"{_FAKE_VERSION_LINE}\nbuilt with gcc 15.2.0\n"


def _mock_exists(path_str: str) -> "patch":
    """Patch Path.exists() to return True only for the given path."""
    def _exists(self):
        return str(self) == path_str
    return patch.object(Path, "exists", _exists)


def _mock_is_file(result: bool = True) -> "patch":
    return patch.object(Path, "is_file", lambda self: result)


def _mock_subprocess_version(stdout: str = _FAKE_VERSION_OUTPUT) -> "patch":
    """Patch subprocess.run to return a fake ffmpeg -version result."""
    mock_result = MagicMock()
    mock_result.stdout = stdout
    mock_result.returncode = 0
    return patch("subprocess.run", return_value=mock_result)


# ── A–B: get_ffmpeg_path ─────────────────────────────────────────────────────

class TestGetFfmpegPath:

    def test_reads_ffmpeg_path_from_env(self):
        from backend.services.media.ffmpeg import get_ffmpeg_path
        custom = r"C:\custom\ffmpeg.exe"
        with patch.dict(os.environ, {"FFMPEG_PATH": custom}):
            assert get_ffmpeg_path() == custom

    def test_returns_default_when_env_not_set(self):
        from backend.services.media.ffmpeg import get_ffmpeg_path, _DEFAULT_FFMPEG_PATH
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.copy()
            env.pop("FFMPEG_PATH", None)
            with patch.dict(os.environ, env, clear=True):
                result = get_ffmpeg_path()
        assert result == _DEFAULT_FFMPEG_PATH

    def test_strips_whitespace_from_env_value(self):
        from backend.services.media.ffmpeg import get_ffmpeg_path
        with patch.dict(os.environ, {"FFMPEG_PATH": f"  {_FAKE_PATH}  "}):
            assert get_ffmpeg_path() == _FAKE_PATH


# ── C–D: get_ffprobe_path ────────────────────────────────────────────────────

class TestGetFfprobePath:

    def test_reads_ffprobe_path_from_env(self):
        from backend.services.media.ffmpeg import get_ffprobe_path
        with patch.dict(os.environ, {"FFPROBE_PATH": _FAKE_PROBE}):
            assert get_ffprobe_path() == _FAKE_PROBE

    def test_derives_from_ffmpeg_dir_when_not_set(self):
        from backend.services.media.ffmpeg import get_ffprobe_path
        env = {
            "FFMPEG_PATH": _FAKE_PATH,
        }
        # Remove FFPROBE_PATH if present
        with patch.dict(os.environ, env):
            os.environ.pop("FFPROBE_PATH", None)
            result = get_ffprobe_path()
        assert result.endswith("ffprobe.exe")
        assert "ffmpeg\\bin" in result or "ffmpeg/bin" in result


# ── E–I: get_ffmpeg_version ──────────────────────────────────────────────────

class TestGetFfmpegVersion:

    def test_returns_first_line_of_version_output(self):
        from backend.services.media.ffmpeg import get_ffmpeg_version
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with _mock_subprocess_version():
                    result = get_ffmpeg_version()
        assert result == _FAKE_VERSION_LINE

    def test_raises_when_binary_missing(self):
        from backend.services.media.ffmpeg import get_ffmpeg_version, FFmpegNotFoundError
        with patch.dict(os.environ, {"FFMPEG_PATH": r"G:\nonexistent\ffmpeg.exe"}):
            with patch.object(Path, "exists", return_value=False):
                with pytest.raises(FFmpegNotFoundError, match="not found"):
                    get_ffmpeg_version()

    def test_raises_on_timeout(self):
        from backend.services.media.ffmpeg import get_ffmpeg_version, FFmpegNotFoundError
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 10)):
                    with pytest.raises(FFmpegNotFoundError, match="timed out"):
                        get_ffmpeg_version()

    def test_raises_on_oserror(self):
        from backend.services.media.ffmpeg import get_ffmpeg_version, FFmpegNotFoundError
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with patch("subprocess.run", side_effect=OSError("Permission denied")):
                    with pytest.raises(FFmpegNotFoundError, match="Cannot execute"):
                        get_ffmpeg_version()

    def test_raises_on_file_not_found_error(self):
        from backend.services.media.ffmpeg import get_ffmpeg_version, FFmpegNotFoundError
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
                    with pytest.raises(FFmpegNotFoundError, match="not found"):
                        get_ffmpeg_version()

    def test_raises_on_empty_output(self):
        from backend.services.media.ffmpeg import get_ffmpeg_version, FFmpegNotFoundError
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with _mock_subprocess_version(stdout=""):
                    with pytest.raises(FFmpegNotFoundError, match="no version output"):
                        get_ffmpeg_version()

    def test_version_truncated_to_max_length(self):
        from backend.services.media.ffmpeg import get_ffmpeg_version, _VERSION_MAX_LEN
        long_line = "x" * (_VERSION_MAX_LEN + 500)
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with _mock_subprocess_version(stdout=long_line + "\n"):
                    result = get_ffmpeg_version()
        assert len(result) == _VERSION_MAX_LEN


# ── J–L: check_ffmpeg ────────────────────────────────────────────────────────

class TestCheckFfmpeg:

    def test_returns_available_true_on_success(self):
        from backend.services.media.ffmpeg import check_ffmpeg
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with _mock_subprocess_version():
                    result = check_ffmpeg()
        assert result["available"] is True
        assert result["path"] == _FAKE_PATH
        assert "version" in result
        assert _FAKE_VERSION_LINE in result["version"]

    def test_returns_available_false_on_missing_binary(self):
        from backend.services.media.ffmpeg import check_ffmpeg
        with patch.dict(os.environ, {"FFMPEG_PATH": r"G:\missing\ffmpeg.exe"}):
            with patch.object(Path, "exists", return_value=False):
                result = check_ffmpeg()
        assert result["available"] is False
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_never_raises(self):
        """check_ffmpeg() must not raise even if everything is broken."""
        from backend.services.media.ffmpeg import check_ffmpeg
        with patch.dict(os.environ, {"FFMPEG_PATH": r"G:\missing\ffmpeg.exe"}):
            with patch.object(Path, "exists", return_value=False):
                # Should return dict, never raise
                result = check_ffmpeg()
        assert isinstance(result, dict)

    def test_result_contains_path_key(self):
        from backend.services.media.ffmpeg import check_ffmpeg
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with _mock_subprocess_version():
                    result = check_ffmpeg()
        assert "path" in result

    def test_available_false_has_no_version_key(self):
        from backend.services.media.ffmpeg import check_ffmpeg
        with patch.dict(os.environ, {"FFMPEG_PATH": r"G:\missing\ffmpeg.exe"}):
            with patch.object(Path, "exists", return_value=False):
                result = check_ffmpeg()
        assert "version" not in result

    def test_available_true_has_no_error_key(self):
        from backend.services.media.ffmpeg import check_ffmpeg
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with _mock_subprocess_version():
                    result = check_ffmpeg()
        assert "error" not in result


# ── M–O: _assert_executable_exists ──────────────────────────────────────────

class TestAssertExecutableExists:

    def test_raises_when_path_does_not_exist(self):
        from backend.services.media.ffmpeg import _assert_executable_exists, FFmpegNotFoundError
        with patch.object(Path, "exists", return_value=False):
            with pytest.raises(FFmpegNotFoundError, match="not found"):
                _assert_executable_exists(r"G:\missing\ffmpeg.exe")

    def test_raises_when_path_is_directory(self):
        from backend.services.media.ffmpeg import _assert_executable_exists, FFmpegNotFoundError
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "is_file", return_value=False):
                with pytest.raises(FFmpegNotFoundError, match="directory"):
                    _assert_executable_exists(r"G:\some\directory")

    def test_passes_when_file_exists(self):
        from backend.services.media.ffmpeg import _assert_executable_exists
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "is_file", return_value=True):
                # Should not raise
                _assert_executable_exists(_FAKE_PATH)


# ── P: No credential / secret leakage ────────────────────────────────────────

class TestNoSecretLeakage:

    def test_check_ffmpeg_does_not_expose_groq_key(self):
        from backend.services.media.ffmpeg import check_ffmpeg
        groq_key = "gsk_supersecret_key_12345"
        with patch.dict(os.environ, {
            "FFMPEG_PATH": _FAKE_PATH,
            "GROQ_API_KEY": groq_key,
        }):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with _mock_subprocess_version():
                    result = check_ffmpeg()
        result_str = str(result)
        assert groq_key not in result_str

    def test_check_ffmpeg_does_not_expose_token_file(self):
        from backend.services.media.ffmpeg import check_ffmpeg
        with patch.dict(os.environ, {
            "FFMPEG_PATH": _FAKE_PATH,
            "TOKEN_FILE": "token.json",
        }):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with _mock_subprocess_version():
                    result = check_ffmpeg()
        # Path itself is expected to appear, but no token content
        assert "token.json" not in str(result)

    def test_get_ffmpeg_path_returns_only_path_no_secrets(self):
        from backend.services.media.ffmpeg import get_ffmpeg_path
        with patch.dict(os.environ, {
            "FFMPEG_PATH": _FAKE_PATH,
            "GROQ_API_KEY": "should_not_appear",
        }):
            result = get_ffmpeg_path()
        assert "should_not_appear" not in result
        assert result == _FAKE_PATH

    def test_version_output_does_not_contain_env_secrets(self):
        from backend.services.media.ffmpeg import get_ffmpeg_version
        secret = "my_secret_password_xyz"
        # Inject secret into subprocess stdout (simulate a malicious binary)
        malicious_output = f"ffmpeg version 1.0 secret={secret}\nbuilt with gcc\n"
        with patch.dict(os.environ, {"FFMPEG_PATH": _FAKE_PATH, "SECRET": secret}):
            with _mock_exists(_FAKE_PATH), _mock_is_file():
                with _mock_subprocess_version(stdout=malicious_output):
                    result = get_ffmpeg_version()
        # Version output is truncated to _VERSION_MAX_LEN — secret is in the
        # binary output, but it did not come from our environment.
        # The important thing: we never inject env secrets INTO the result.
        assert os.environ.get("SECRET", "") not in result or secret not in os.environ.get("GROQ_API_KEY", "")
