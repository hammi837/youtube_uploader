"""
backend/services/video/ffmpeg_assembler.py — FFmpeg-based video assembler.

Responsibilities:
  1. Convert each scene image to a short video clip with Ken Burns effect.
  2. Concatenate scene clips.
  3. Mix in narration audio.
  4. Mix in optional background music.
  5. Burn in SRT captions.
  6. Encode final 1080p30 H.264/AAC MP4.
  7. Verify output with ffprobe.

All FFmpeg calls use the configured FFMPEG_PATH — never system PATH.
All outputs go to G: drive — never C:.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ffmpeg() -> str:
    from backend.services.media.ffmpeg import get_ffmpeg_path, FFmpegNotFoundError
    path = get_ffmpeg_path()
    if not Path(path).exists():
        raise FFmpegNotFoundError(
            f"FFmpeg not found at: {path}. Set FFMPEG_PATH in backend/.env."
        )
    return path


def _ffprobe() -> str:
    from backend.services.media.ffmpeg import get_ffprobe_path
    return get_ffprobe_path()


def _run(cmd: list[str], timeout: int = 600, label: str = "ffmpeg") -> str:
    """
    Run an FFmpeg/FFprobe command, return stdout.
    Raises FFmpegError with safe truncated stderr on failure.
    """
    from backend.services.video.exceptions import FFmpegError
    logger.debug("Running %s: %s", label, " ".join(cmd[:8]) + " ...")
    t0 = time.perf_counter()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.perf_counter() - t0
    if result.returncode != 0:
        stderr_safe = result.stderr[-1000:] if result.stderr else "(no output)"
        raise FFmpegError(
            f"{label} failed (rc={result.returncode}) after {elapsed:.1f}s. "
            f"stderr: {stderr_safe}"
        )
    logger.debug("%s completed in %.1fs", label, elapsed)
    return result.stdout


# ── Scene timing ──────────────────────────────────────────────────────────────

@dataclass
class SceneClip:
    scene_number: int
    image_path: Path
    duration_seconds: float
    narration_text: str = ""

    # Ken Burns parameters — set during assembly
    kb_zoom_in: bool = True
    zoom_start: float = 1.0
    zoom_end: float = 1.05


def calculate_scene_durations(
    scenes: list[dict],
    total_audio_duration: float,
) -> list[float]:
    """
    Distribute total audio duration across scenes proportional to
    each scene's narration character count.

    Falls back to equal distribution if character counts are all zero.
    """
    char_counts = []
    for scene in scenes:
        narration = scene.get("narration", "") or ""
        char_counts.append(max(len(narration), 1))

    total_chars = sum(char_counts)
    durations = [
        max(1.0, (c / total_chars) * total_audio_duration)
        for c in char_counts
    ]

    # Normalise so sum == total_audio_duration
    current_sum = sum(durations)
    if current_sum > 0:
        factor = total_audio_duration / current_sum
        durations = [d * factor for d in durations]

    return durations


# ── Ken Burns filter ──────────────────────────────────────────────────────────

_KB_EFFECTS = [
    # zoom in slowly
    "zoompan=z='min(zoom+0.0008,1.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    # zoom out slowly
    "zoompan=z='if(eq(on\\,1)\\,1.05\\,max(zoom-0.0008\\,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    # mild zoom + shift right (x offset grows with frame number)
    "zoompan=z='1.04':x='iw/2-(iw/zoom/2)+on*1.5':y='ih/2-(ih/zoom/2)'",
    # mild zoom + shift left
    "zoompan=z='1.04':x='iw/2-(iw/zoom/2)-on*1.5':y='ih/2-(ih/zoom/2)'",
]


def _ken_burns_filter(scene_idx: int, duration: float, w: int, h: int) -> str:
    """Pick a Ken Burns effect and return the zoompan filter string."""
    effect = _KB_EFFECTS[scene_idx % len(_KB_EFFECTS)]
    fps = 30
    total_frames = int(duration * fps) + 2
    # zoompan needs d=total_frames, s=WxH
    return f"{effect}:d={total_frames}:s={w}x{h},fps={fps}"


# ── Per-scene clip builder ─────────────────────────────────────────────────────

def build_scene_clip(
    clip: SceneClip,
    output_path: Path,
    width: int,
    height: int,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> Path:
    """
    Convert a single scene image into a short video clip.

    Applies:
      - scale/pad to target resolution
      - Ken Burns zoom/pan effect
      - 30 fps
      - no audio (audio added at final mix)

    Returns path to the generated clip.
    """
    ff = _ffmpeg()
    duration = max(clip.duration_seconds, 1.0)
    fps = 30
    total_frames = int(duration * fps) + 2

    kb = _ken_burns_filter(clip.scene_number - 1, duration, width, height)

    # scale to fill, then pad/crop to exact size
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"{kb}"
    )

    cmd = [
        ff, "-y",
        "-loop", "1",
        "-i", str(clip.image_path),
        "-vf", vf,
        "-t", str(duration),
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "ultrafast",   # speed > quality for intermediate
        "-crf", "28",
        "-an",
        str(output_path),
    ]
    _run(cmd, timeout=300, label=f"scene_{clip.scene_number}_clip")
    return output_path


# ── Concatenation ─────────────────────────────────────────────────────────────

def concatenate_clips(
    clip_paths: list[Path],
    output_path: Path,
    temp_dir: Path,
) -> Path:
    """Concatenate video clips using FFmpeg concat demuxer."""
    ff = _ffmpeg()

    concat_file = temp_dir / "concat.txt"
    lines = []
    for cp in clip_paths:
        # Use forward slashes for FFmpeg on Windows
        lines.append(f"file '{str(cp).replace(chr(92), '/')}'")
    concat_file.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        ff, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(output_path),
    ]
    _run(cmd, timeout=300, label="concat_clips")
    return output_path


# ── Audio mix ─────────────────────────────────────────────────────────────────

def mix_audio(
    video_path: Path,
    narration_path: Path,
    music_path: Optional[Path],
    output_path: Path,
    music_volume: float = 0.08,
) -> Path:
    """
    Add narration (and optional background music) to the concatenated video.

    - Narration is full volume.
    - Music is looped/trimmed to match narration and mixed at music_volume.
    """
    ff = _ffmpeg()
    video_duration = _get_duration(str(video_path))

    if music_path and music_path.exists():
        # Mix narration + music under it
        # adelay/aloop music to match duration; amix with weights
        audio_filter = (
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=duration={video_duration:.3f},"
            f"volume={music_volume:.3f}[music];"
            f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            ff, "-y",
            "-i", str(narration_path),
            "-i", str(music_path),
            "-filter_complex", audio_filter,
            "-map", "0:v:0",  # will be overridden below — we re-add video
            "-i", str(video_path),
            "-map", "2:v:0",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]
        # Simpler approach: separate video+narration merge, then add music
        # Use two-step to avoid complex filter graph issues on older FFmpeg
        narration_only = output_path.with_suffix(".naronly.mp4")
        _merge_video_audio(video_path, narration_path, narration_only)
        _mix_music(narration_only, music_path, output_path, music_volume, video_duration)
        narration_only.unlink(missing_ok=True)
    else:
        _merge_video_audio(video_path, narration_path, output_path)

    return output_path


def _merge_video_audio(video_path: Path, audio_path: Path, output_path: Path) -> Path:
    """Merge a silent video with a narration audio track."""
    ff = _ffmpeg()
    cmd = [
        ff, "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]
    _run(cmd, timeout=300, label="merge_video_audio")
    return output_path


def _mix_music(
    video_with_narration: Path,
    music_path: Path,
    output_path: Path,
    music_volume: float,
    video_duration: float,
) -> Path:
    ff = _ffmpeg()
    audio_filter = (
        f"[1:a]aloop=loop=-1:size=2e+09,atrim=duration={video_duration:.3f},"
        f"volume={music_volume:.3f}[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=3[aout]"
    )
    cmd = [
        ff, "-y",
        "-i", str(video_with_narration),
        "-i", str(music_path),
        "-filter_complex", audio_filter,
        "-map", "0:v:0",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]
    _run(cmd, timeout=300, label="mix_music")
    return output_path


# ── Caption burning ───────────────────────────────────────────────────────────

def burn_captions(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    width: int,
    height: int,
) -> Path:
    """
    Burn SRT subtitles into the video using the FFmpeg subtitles filter.
    """
    ff = _ffmpeg()

    if not srt_path.exists() or srt_path.stat().st_size == 0:
        logger.warning("SRT is empty or missing — skipping caption burn")
        import shutil
        shutil.copy2(str(video_path), str(output_path))
        return output_path

    font_size = max(28, int(height * 0.035))

    # FFmpeg on Windows needs escaped path with forward slashes
    srt_safe = str(srt_path).replace("\\", "/").replace(":", "\\:")

    subtitle_filter = (
        f"subtitles='{srt_safe}':"
        f"force_style='FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"   # white text
        f"OutlineColour=&H00000000,"   # black outline
        f"BackColour=&H80000000,"      # semi-transparent shadow
        f"Outline=2,Shadow=1,"
        f"MarginV=60,"
        f"Alignment=2'"                # bottom-center
    )

    cmd = [
        ff, "-y",
        "-i", str(video_path),
        "-vf", subtitle_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd, timeout=600, label="burn_captions")
    return output_path


# ── Final encode ──────────────────────────────────────────────────────────────

def final_encode(
    input_path: Path,
    output_path: Path,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    crf: int = 20,
) -> Path:
    """
    Final H.264/AAC encode with faststart for web streaming.
    Re-encodes to ensure consistent output format.
    """
    ff = _ffmpeg()
    cmd = [
        ff, "-y",
        "-i", str(input_path),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd, timeout=900, label="final_encode")
    return output_path


# ── Probe / verify ────────────────────────────────────────────────────────────

def _get_duration(file_path: str) -> float:
    """Return media duration in seconds using ffprobe."""
    try:
        fp = _ffprobe()
        result = subprocess.run(
            [fp, "-v", "quiet", "-print_format", "json",
             "-show_streams", file_path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            dur = stream.get("duration")
            if dur:
                return float(dur)
    except Exception:
        pass
    return 0.0


def probe_video(file_path: str | Path) -> dict:
    """
    Run ffprobe on the output file and return a summary dict.
    Used for verification after assembly.
    """
    fp = _ffprobe()
    result = subprocess.run(
        [fp, "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(file_path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return {"error": result.stderr[:200]}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "ffprobe returned invalid JSON"}

    summary = {
        "file": str(file_path),
        "format": data.get("format", {}).get("format_name", "unknown"),
        "duration": float(data.get("format", {}).get("duration", 0)),
        "size_bytes": int(data.get("format", {}).get("size", 0)),
        "streams": [],
    }
    for s in data.get("streams", []):
        entry = {
            "codec_type": s.get("codec_type"),
            "codec_name": s.get("codec_name"),
            "width": s.get("width"),
            "height": s.get("height"),
            "r_frame_rate": s.get("r_frame_rate"),
            "duration": s.get("duration"),
        }
        summary["streams"].append(entry)

    return summary
