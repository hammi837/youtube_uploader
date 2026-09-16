"""
Video background processing with FFmpeg (Phase 3E.4).

Handles local video clip processing: trim, loop, crop, and aspect ratio adaptation.
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from backend.services.media.ffmpeg import get_ffmpeg_path, FFmpegNotFoundError
from backend.services.video.background_config import (
    BackgroundConfig,
    BackgroundType,
    BackgroundFit,
)

logger = logging.getLogger(__name__)


def process_video_background(
    video_path: Path,
    output_path: Path,
    target_duration: float,
    width: int,
    height: int,
    background_config: BackgroundConfig,
) -> Optional[Path]:
    """
    Process a local video background for a scene.

    Handles:
    - Trimming if video is longer than target duration
    - Looping if video is shorter than target duration
    - Scaling and cropping to target aspect ratio
    - No audio from background clip

    Args:
        video_path: Path to the source video
        output_path: Path for the processed output video
        target_duration: Target scene duration in seconds
        width: Target width
        height: Target height
        background_config: Background configuration

    Returns:
        Path to processed video if successful, None otherwise.
    """
    try:
        ffmpeg_path = get_ffmpeg_path()
        
        # Build FFmpeg filter chain
        filters = []
        
        # 1. Scale and crop to target aspect ratio
        if background_config.background_fit == BackgroundFit.COVER:
            # Scale to cover then crop to target dimensions
            filters.append(f"scale=ow=ih*{width}/{height}:oh=ih:force_original_aspect_ratio=decrease")
            filters.append(f"crop={width}:{height}")
        elif background_config.background_fit == BackgroundFit.CONTAIN:
            # Scale to fit within bounds, letterbox with black
            filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
            filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")
        elif background_config.background_fit in (BackgroundFit.FILL, BackgroundFit.STRETCH):
            # Stretch to fill (may distort)
            filters.append(f"scale={width}:{height}")
        
        # 2. Trim or loop to target duration
        # For now, use simple stream copy with duration limit
        # Complex looping requires multiple passes
        filter_string = ",".join(filters)
        
        # Build FFmpeg command
        cmd = [
            str(ffmpeg_path),
            "-i", str(video_path),
            "-ss", str(background_config.background_start_time),
            "-t", str(target_duration),
            "-vf", filter_string,
            "-an",  # No audio from background
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-y",  # Overwrite output
            str(output_path),
        ]
        
        logger.info(f"Processing video background: {video_path.name} -> {output_path.name}")
        logger.debug(f"FFmpeg command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout per scene
        )
        
        if result.returncode != 0:
            logger.warning(f"FFmpeg failed for video background: {result.stderr}")
            return None
        
        if output_path.exists():
            logger.info(f"Video background processed successfully: {output_path.name}")
            return output_path
        else:
            logger.warning(f"Output file not created: {output_path}")
            return None
            
    except FFmpegNotFoundError:
        logger.error("FFmpeg not found, cannot process video background")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"FFmpeg timeout processing video background: {video_path}")
        return None
    except Exception as e:
        logger.warning(f"Failed to process video background: {e}")
        return None


def loop_video_to_duration(
    video_path: Path,
    output_path: Path,
    target_duration: float,
    max_loops: int = 10,
) -> Optional[Path]:
    """
    Loop a video to match target duration.

    Uses FFmpeg's stream loop filter for efficient looping.

    Args:
        video_path: Path to the source video
        output_path: Path for the looped output
        target_duration: Target duration in seconds
        max_loops: Maximum number of loops to prevent infinite loops

    Returns:
        Path to looped video if successful, None otherwise.
    """
    try:
        ffmpeg_path = get_ffmpeg_path()
        
        # Calculate required loops
        # For simplicity, use stream loop filter
        # stream=loop=loop=count:size=start
        loop_count = int(target_duration // 5) + 1  # Estimate based on 5s clips
        loop_count = min(loop_count, max_loops)
        
        cmd = [
            str(ffmpeg_path),
            "-stream_loop", str(loop_count),
            "-i", str(video_path),
            "-t", str(target_duration),
            "-c", "copy",  # Stream copy for speed
            "-y",
            str(output_path),
        ]
        
        logger.info(f"Looping video: {video_path.name} (loops: {loop_count})")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        
        if result.returncode != 0:
            logger.warning(f"FFmpeg loop failed: {result.stderr}")
            return None
        
        if output_path.exists():
            return output_path
        return None
            
    except Exception as e:
        logger.warning(f"Failed to loop video: {e}")
        return None


def apply_video_background_to_scene(
    scene_card_path: Path,
    video_path: Path,
    output_path: Path,
    target_duration: float,
    width: int,
    height: int,
    background_config: BackgroundConfig,
) -> Optional[Path]:
    """
    Apply a video background behind a scene card.

    Composite the video background with the scene card overlay.

    Args:
        scene_card_path: Path to the scene card PNG
        video_path: Path to the video background
        output_path: Path for the final composited video
        target_duration: Target scene duration
        width: Target width
        height: Target height
        background_config: Background configuration

    Returns:
        Path to composited video if successful, None otherwise.
    """
    try:
        ffmpeg_path = get_ffmpeg_path()
        
        # First, process the video background
        temp_video = output_path.parent / f"{output_path.stem}_bg{output_path.suffix}"
        processed_bg = process_video_background(
            video_path,
            temp_video,
            target_duration,
            width,
            height,
            background_config,
        )
        
        if not processed_bg:
            return None
        
        # Overlay scene card on video
        # Use overlay filter with scene card as PNG overlay
        cmd = [
            str(ffmpeg_path),
            "-i", str(processed_bg),
            "-i", str(scene_card_path),
            "-filter_complex",
            f"[1:v]format=rgba,colorchannelmixer=aa={1.0 - background_config.overlay_opacity}[ov];[0:v][ov]overlay=0:0",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",  # No audio from background
            "-t", str(target_duration),
            "-y",
            str(output_path),
        ]
        
        logger.info(f"Compositing video background with scene card")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        
        # Clean up temp file
        if temp_video.exists():
            temp_video.unlink()
        
        if result.returncode != 0:
            logger.warning(f"FFmpeg composite failed: {result.stderr}")
            return None
        
        if output_path.exists():
            return output_path
        return None
            
    except Exception as e:
        logger.warning(f"Failed to apply video background: {e}")
        return None
