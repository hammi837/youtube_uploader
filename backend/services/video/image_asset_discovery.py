"""
Image asset discovery and metadata extraction (Phase 3F.1).

Scans the local images directory and extracts metadata using Pillow.
"""

import hashlib
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

try:
    from PIL import Image
except ImportError:
    Image = None

logger = logging.getLogger(__name__)


@dataclass
class ImageMetadata:
    """Metadata for an image asset."""
    path: str
    filename: str
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
    
    @property
    def aspect_ratio(self) -> float:
        """Calculate aspect ratio as width/height."""
        if self.width is None or self.height is None or self.height == 0:
            return 0.0
        return self.width / self.height


def extract_image_metadata(image_path: Path) -> Optional[ImageMetadata]:
    """
    Extract metadata from an image file using Pillow.

    Args:
        image_path: Path to the image file

    Returns:
        ImageMetadata if successful, None if extraction fails.
    """
    if Image is None:
        logger.error("PIL not installed, cannot extract image metadata")
        return None
    
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            if width <= 0 or height <= 0:
                logger.warning(f"Invalid dimensions for {image_path}: {width}x{height}")
                return None
            
            return ImageMetadata(
                path=str(image_path),
                filename=image_path.name,
                width=width,
                height=height,
            )
            
    except Exception as e:
        logger.warning(f"Failed to extract metadata from {image_path}: {e}")
        return None


def discover_image_assets(
    images_dir: Optional[Path] = None,
    recursive: bool = True,
) -> List[ImageMetadata]:
    """
    Discover image assets in the configured directory.

    Args:
        images_dir: Directory to scan (default: assets/backgrounds/images/)
        recursive: Whether to scan subdirectories (default: True)

    Returns:
        List of ImageMetadata for discovered images.
    """
    if images_dir is None:
        from backend.services.video.background_config import IMAGES_DIR
        images_dir = IMAGES_DIR
    
    # Support Path or string input
    if isinstance(images_dir, str):
        images_dir = Path(images_dir)
    
    if not images_dir.exists():
        logger.info(f"Images directory does not exist: {images_dir}")
        return []
    
    images = []
    
    # Scan for image files
    if recursive:
        image_files = images_dir.rglob("*")
    else:
        image_files = images_dir.glob("*")
    
    for file_path in image_files:
        if not file_path.is_file():
            continue
        
        # Skip hidden files
        if file_path.name.startswith('.'):
            continue
        
        # Check file extension
        from backend.services.video.background_config import SUPPORTED_IMAGE_EXTENSIONS
        if file_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        
        # Extract metadata
        metadata = extract_image_metadata(file_path)
        if metadata:
            images.append(metadata)
            logger.debug(f"Discovered image: {metadata.filename} ({metadata.width}x{metadata.height})")
    
    logger.info(f"Discovered {len(images)} image assets in {images_dir}")
    return images


def get_image_by_path(image_path: str) -> Optional[ImageMetadata]:
    """
    Get metadata for a specific image by path.

    Args:
        image_path: Path to the image file

    Returns:
        ImageMetadata if file exists and is valid, None otherwise.
    """
    path = Path(image_path)
    if not path.exists():
        return None
    
    return extract_image_metadata(path)


def select_auto_images(
    job_id: str,
    scene_count: int,
    aspect_ratio: str,
) -> List[Optional[str]]:
    """
    Select image backgrounds for scenes using stable deterministic selection (Phase 3F.1).

    Uses hashlib.sha256 for stable seeding across process restarts and
    deterministically shuffles assets to vary backgrounds across scenes.

    Args:
        job_id: Queue job ID (used as stable seed)
        scene_count: Number of scenes
        aspect_ratio: Target aspect ratio (16:9 or 9:16)

    Returns:
        List of image paths (one per scene) or None for gradient fallback.

    Algorithm:
        1. Discover all available images
        2. Filter valid images (exclude zero dimensions, unreadable, etc.)
        3. Filter by aspect ratio compatibility (prefer matching)
        4. Create stable seed from job_id using hashlib.sha256
        5. Deterministically shuffle using local Random instance
        6. Assign scenes from shuffled list (reuse if needed)
        7. Fallback to gradient if no valid images

    Example:
        job_id = "abc123"
        scene_count = 5
        aspect_ratio = "16:9"
        Available images: [landscape1.jpg, landscape2.jpg, portrait1.jpg]

        Step 1: Filter by aspect ratio → [landscape1.jpg, landscape2.jpg]
        Step 2: Stable seed = sha256("abc123") → integer
        Step 3: Shuffle deterministically → [landscape2.jpg, landscape1.jpg]
        Step 4: Assign to scenes → [landscape2.jpg, landscape1.jpg, landscape2.jpg, landscape1.jpg, landscape2.jpg]

        Result: Scene 1 uses landscape2.jpg, Scene 2 uses landscape1.jpg, etc.
    """
    # 1. Discover all available images
    all_images = discover_image_assets()

    # 2. Filter valid images (exclude zero dimensions, unreadable, etc.)
    valid_images = [img for img in all_images if img.width and img.height and img.width > 0 and img.height > 0]

    # 3. Filter by aspect ratio compatibility (prefer matching)
    if aspect_ratio == "16:9":
        # Prefer landscape images, fall back to all valid
        ratio_filtered = [img for img in valid_images if img.orientation == "landscape"]
        if not ratio_filtered:
            ratio_filtered = valid_images
    elif aspect_ratio == "9:16":
        # Prefer portrait images, fall back to all valid
        ratio_filtered = [img for img in valid_images if img.orientation == "portrait"]
        if not ratio_filtered:
            ratio_filtered = valid_images
    else:
        ratio_filtered = valid_images

    # 4. Fallback to gradient if no valid images
    if not ratio_filtered:
        logger.info(f"No valid image assets available for auto-selection, falling back to gradient")
        return [None] * scene_count

    # 5. Create stable seed from job_id using hashlib
    seed_bytes = hashlib.sha256(job_id.encode("utf-8")).digest()
    seed_int = int.from_bytes(seed_bytes, byteorder='big')

    # 6. Deterministically shuffle using local Random instance
    rng = random.Random(seed_int)
    shuffled_images = ratio_filtered.copy()
    rng.shuffle(shuffled_images)

    logger.info(f"Auto-selection: {len(shuffled_images)} valid images for {scene_count} scenes, aspect_ratio={aspect_ratio}")

    # 7. Assign scenes from shuffled list (reuse if needed)
    selections = []
    for scene_num in range(scene_count):
        index = scene_num % len(shuffled_images)
        selections.append(shuffled_images[index].path)
        logger.debug(f"Scene {scene_num + 1}: {shuffled_images[index].filename}")

    return selections
