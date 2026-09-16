"""
Video asset discovery and metadata extraction (Phase 3E.4).

Scans the local videos directory and extracts metadata using FFprobe.
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from backend.services.media.ffmpeg import get_ffprobe_path, FFmpegNotFoundError

logger = logging.getLogger(__name__)


@dataclass
class VideoMetadata:
    """Metadata for a video asset."""
    path: str
    filename: str
    duration: Optional[float] = None  # Duration in seconds
    width: Optional[int] = None
    height: Optional[int] = None
    
    @property
    def orientation(self) -> str:
        """Calculate orientation from dimensions."""
        if self.width is None or self.height is None:
            return "unknown"
        if self.width > self.height:
            return "landscape"
        elif self.height > self.width:
            return "portrait"
        else:
            return "square"


def extract_video_metadata(video_path: Path) -> Optional[VideoMetadata]:
    """
    Extract metadata from a video file using FFprobe.

    Args:
        video_path: Path to the video file

    Returns:
        VideoMetadata if successful, None if extraction fails.
    """
    try:
        ffprobe_path = get_ffprobe_path()
        
        # Use ffprobe to get video stream information
        cmd = [
            str(ffprobe_path),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        
        if result.returncode != 0:
            logger.warning(f"FFprobe failed for {video_path}: {result.stderr}")
            return None
        
        # Parse output: width\nheight\nduration
        lines = result.stdout.strip().split('\n')
        if len(lines) < 3:
            logger.warning(f"Unexpected ffprobe output for {video_path}")
            return None
        
        try:
            width = int(lines[0])
            height = int(lines[1])
            duration = float(lines[2])
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse ffprobe output for {video_path}: {e}")
            return None
        
        return VideoMetadata(
            path=str(video_path),
            filename=video_path.name,
            duration=duration,
            width=width,
            height=height,
        )
        
    except FFmpegNotFoundError:
        logger.error("FFprobe not found, cannot extract video metadata")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"FFprobe timeout for {video_path}")
        return None
    except Exception as e:
        logger.warning(f"Failed to extract metadata from {video_path}: {e}")
        return None


def discover_video_assets(
    videos_dir: Optional[Path] = None,
    recursive: bool = True,
) -> List[VideoMetadata]:
    """
    Discover video assets in the configured directory.

    Args:
        videos_dir: Directory to scan (default: assets/backgrounds/videos/)
        recursive: Whether to scan subdirectories (default: True)

    Returns:
        List of VideoMetadata for discovered videos.
    """
    if videos_dir is None:
        from backend.services.video.background_config import VIDEOS_DIR
        videos_dir = VIDEOS_DIR
    
    if not videos_dir.exists():
        logger.info(f"Videos directory does not exist: {videos_dir}")
        return []
    
    videos = []
    
    # Scan for video files
    if recursive:
        video_files = videos_dir.rglob("*")
    else:
        video_files = videos_dir.glob("*")
    
    for file_path in video_files:
        if not file_path.is_file():
            continue
        
        # Skip hidden files
        if file_path.name.startswith('.'):
            continue
        
        # Check file extension
        from backend.services.video.background_config import SUPPORTED_VIDEO_EXTENSIONS
        if file_path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            continue
        
        # Extract metadata
        metadata = extract_video_metadata(file_path)
        if metadata:
            videos.append(metadata)
            logger.debug(f"Discovered video: {metadata.filename} ({metadata.width}x{metadata.height}, {metadata.duration:.1f}s)")
    
    logger.info(f"Discovered {len(videos)} video assets in {videos_dir}")
    return videos


def get_video_by_path(video_path: str) -> Optional[VideoMetadata]:
    """
    Get metadata for a specific video by path.

    Args:
        video_path: Path to the video file

    Returns:
        VideoMetadata if file exists and is valid, None otherwise.
    """
    path = Path(video_path)
    if not path.exists():
        return None
    
    return extract_video_metadata(path)


def select_video_by_index(
    videos: List[VideoMetadata],
    index: int,
) -> Optional[VideoMetadata]:
    """
    Select a video by index with wraparound.

    Args:
        videos: List of available videos
        index: Index to select (supports negative indexing)

    Returns:
        Selected video or None if list is empty.
    """
    if not videos:
        return None
    
    # Normalize index
    index = index % len(videos)
    return videos[index]
