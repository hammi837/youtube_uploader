"""
verify_real_thumbnails.py — Real thumbnail engine verification using actual MP4 files.

Tests Phase 3J thumbnail generation with real video input:
1. scene_frame (16:9 video)
2. scene_frame_overlay (16:9 video)
3. NULL/default (16:9 video)
4. explicit text_only (16:9 video)
5. 9:16 scene_frame_overlay (portrait video)

For each test:
- Uses actual MP4 input
- Calls real thumbnail generation code
- Verifies output JPEG exists and is valid
- Verifies dimensions are exactly 1280x720
- Performs manual visual inspection
"""

import sys
from pathlib import Path
from PIL import Image

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from backend.services.video.thumbnail import generate_thumbnail_from_frame, generate_thumbnail
from backend.services.video.media_utils import output_thumbnail_path


def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def verify_thumbnail_file(thumb_path, test_name):
    """Verify thumbnail file properties."""
    print(f"\n{test_name} Thumbnail File Verification:")
    print(f"  Path: {thumb_path}")
    print(f"  Exists: {thumb_path.exists()}")
    
    if not thumb_path.exists():
        print(f"  [FAIL] Thumbnail file does not exist")
        return False
    
    print(f"  File size: {thumb_path.stat().st_size} bytes")
    
    try:
        img = Image.open(thumb_path)
        width, height = img.size
        img.close()
        
        print(f"  Dimensions: {width}x{height}")
        print(f"  Format: {img.format}")
        
        if width == 1280 and height == 720:
            print(f"  [PASS] Dimensions are correct (1280x720)")
        else:
            print(f"  [FAIL] Dimensions incorrect (expected 1280x720)")
            return False
            
        if img.format == "JPEG":
            print(f"  [PASS] Format is JPEG")
        else:
            print(f"  [FAIL] Format is not JPEG (got {img.format})")
            return False
            
        if thumb_path.stat().st_size > 0:
            print(f"  [PASS] File size > 0")
        else:
            print(f"  [FAIL] File size is 0")
            return False
            
        return True
    except Exception as e:
        print(f"  [FAIL] Could not verify image: {e}")
        return False


def manual_visual_inspection(thumb_path, test_name, expected_behavior):
    """Prompt for manual visual inspection and record results."""
    print(f"\n{test_name} Manual Visual Inspection:")
    print(f"  File: {thumb_path}")
    print(f"  Expected behavior: {expected_behavior}")
    print(f"\nPlease open the file and verify:")
    print(f"  • Frame is visible (not black/blank)")
    print(f"  • Title text is readable")
    print(f"  • No severe crop")
    print(f"  • No text clipping")
    print(f"  • Overlay is readable (if applicable)")
    print(f"  • Overall looks usable as YouTube thumbnail")
    
    # Try to open the image with default viewer for manual inspection
    try:
        import os
        os.startfile(str(thumb_path))
        print(f"  [INFO] Opening file in default image viewer...")
    except Exception as e:
        print(f"  [INFO] Could not auto-open file: {e}")
    
    # Record that manual inspection is required
    print(f"\n  [PENDING] Manual visual inspection required")
    print(f"  File opened for inspection: {thumb_path}")
    print(f"  Automated verification completed - user must manually verify visual quality")
    
    # For automated testing, we'll pass this test but record it as pending manual inspection
    return True


def test_scene_frame_16_9():
    """Test 1: scene_frame with 16:9 video."""
    print_section("TEST 1: scene_frame (16:9 video)")
    
    video_path = Path("G:/youtube-uploader/assets/backgrounds/videos/15406860_1920_1080_25fps.mp4")
    output_path = Path("G:/youtube-uploader/data/temp/test_scene_frame_16_9.jpg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"Input video: {video_path}")
    print(f"Output thumbnail: {output_path}")
    
    try:
        thumb_result, fallback_actions = generate_thumbnail_from_frame(
            video_path=video_path,
            title="Test Scene Frame 16:9",
            hook="Representative frame from 16:9 video",
            output_path=output_path,
            style="scene_frame"
        )
        
        print(f"Thumbnail generation completed")
        print(f"Fallback actions: {fallback_actions}")
        
        if verify_thumbnail_file(output_path, "TEST 1"):
            manual_visual_inspection(
                output_path,
                "TEST 1",
                "Representative frame from 16:9 video with gradient overlay"
            )
            return True
        return False
    except Exception as e:
        print(f"[FAIL] Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_scene_frame_overlay_16_9():
    """Test 2: scene_frame_overlay with 16:9 video."""
    print_section("TEST 2: scene_frame_overlay (16:9 video)")
    
    video_path = Path("G:/youtube-uploader/assets/backgrounds/videos/15406860_1920_1080_25fps.mp4")
    output_path = Path("G:/youtube-uploader/data/temp/test_scene_frame_overlay_16_9.jpg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"Input video: {video_path}")
    print(f"Output thumbnail: {output_path}")
    
    try:
        thumb_result, fallback_actions = generate_thumbnail_from_frame(
            video_path=video_path,
            title="Test Scene Frame Overlay 16:9",
            hook="Representative frame with blended overlay",
            output_path=output_path,
            style="scene_frame_overlay"
        )
        
        print(f"Thumbnail generation completed")
        print(f"Fallback actions: {fallback_actions}")
        
        if verify_thumbnail_file(output_path, "TEST 2"):
            manual_visual_inspection(
                output_path,
                "TEST 2",
                "Representative frame blended with gradient overlay"
            )
            return True
        return False
    except Exception as e:
        print(f"[FAIL] Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_null_default():
    """Test 3: NULL/default (resolves to text_only)."""
    print_section("TEST 3: NULL/default (text_only)")
    
    video_path = Path("G:/youtube-uploader/assets/backgrounds/videos/15406860_1920_1080_25fps.mp4")
    output_path = Path("G:/youtube-uploader/data/temp/test_null_default.jpg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"Input video: {video_path}")
    print(f"Output thumbnail: {output_path}")
    print(f"Style: NULL (should resolve to text_only)")
    
    try:
        thumb_result, fallback_actions = generate_thumbnail_from_frame(
            video_path=video_path,
            title="Test NULL Default",
            hook="Text-only thumbnail from default resolution",
            output_path=output_path,
            style=None  # NULL - should resolve to text_only
        )
        
        print(f"Thumbnail generation completed")
        print(f"Fallback actions: {fallback_actions}")
        
        if verify_thumbnail_file(output_path, "TEST 3"):
            manual_visual_inspection(
                output_path,
                "TEST 3",
                "Existing text-only gradient thumbnail (no frame)"
            )
            return True
        return False
    except Exception as e:
        print(f"[FAIL] Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_explicit_text_only():
    """Test 4: explicit text_only."""
    print_section("TEST 4: explicit text_only")
    
    video_path = Path("G:/youtube-uploader/assets/backgrounds/videos/15406860_1920_1080_25fps.mp4")
    output_path = Path("G:/youtube-uploader/data/temp/test_explicit_text_only.jpg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"Input video: {video_path}")
    print(f"Output thumbnail: {output_path}")
    print(f"Style: text_only (explicit)")
    
    try:
        thumb_result, fallback_actions = generate_thumbnail_from_frame(
            video_path=video_path,
            title="Test Explicit Text Only",
            hook="Explicit text-only thumbnail",
            output_path=output_path,
            style="text_only"
        )
        
        print(f"Thumbnail generation completed")
        print(f"Fallback actions: {fallback_actions}")
        
        if verify_thumbnail_file(output_path, "TEST 4"):
            manual_visual_inspection(
                output_path,
                "TEST 4",
                "Existing text-only gradient thumbnail (no frame)"
            )
            return True
        return False
    except Exception as e:
        print(f"[FAIL] Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_scene_frame_overlay_9_16():
    """Test 5: scene_frame_overlay with 9:16 portrait video."""
    print_section("TEST 5: scene_frame_overlay (9:16 portrait video)")
    
    video_path = Path("G:/youtube-uploader/assets/backgrounds/videos/15341287_1080_1920_30fps.mp4")
    output_path = Path("G:/youtube-uploader/data/temp/test_scene_frame_overlay_9_16.jpg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"Input video: {video_path}")
    print(f"Output thumbnail: {output_path}")
    print(f"Source aspect ratio: 9:16 (portrait)")
    print(f"Output aspect ratio: 16:9 (landscape)")
    
    try:
        thumb_result, fallback_actions = generate_thumbnail_from_frame(
            video_path=video_path,
            title="Test 9:16 Scene Frame Overlay",
            hook="Portrait video converted to landscape thumbnail",
            output_path=output_path,
            style="scene_frame_overlay"
        )
        
        print(f"Thumbnail generation completed")
        print(f"Fallback actions: {fallback_actions}")
        
        if verify_thumbnail_file(output_path, "TEST 5"):
            manual_visual_inspection(
                output_path,
                "TEST 5",
                "Portrait 9:16 source converted to 1280x720 landscape thumbnail with overlay"
            )
            return True
        return False
    except Exception as e:
        print(f"[FAIL] Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all real thumbnail verification tests."""
    print("=" * 60)
    print("Phase 3J Real Thumbnail Engine Verification")
    print("=" * 60)
    print("Using actual MP4 files to test thumbnail generation")
    
    results = {}
    
    try:
        results["TEST 1: scene_frame (16:9)"] = test_scene_frame_16_9()
        results["TEST 2: scene_frame_overlay (16:9)"] = test_scene_frame_overlay_16_9()
        results["TEST 3: NULL/default"] = test_null_default()
        results["TEST 4: explicit text_only"] = test_explicit_text_only()
        results["TEST 5: scene_frame_overlay (9:16)"] = test_scene_frame_overlay_9_16()
    except KeyboardInterrupt:
        print("\nTests interrupted by user")
        return
    except Exception as e:
        print(f"\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Print summary
    print_section("Verification Summary")
    
    for test_name, passed in results.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{test_name}: {status}")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed
    
    print(f"\nTotal: {total} | Passed: {passed} | Failed: {failed}")
    
    if failed == 0:
        print("\n[PASS] All thumbnail generation tests completed successfully")
        print("[NOTE] Manual visual inspection required for all generated thumbnails")
    else:
        print(f"\n[FAIL] {failed} test(s) failed")
    
    print("\nGenerated thumbnail files:")
    print("  G:/youtube-uploader/data/temp/test_scene_frame_16_9.jpg")
    print("  G:/youtube-uploader/data/temp/test_scene_frame_overlay_16_9.jpg")
    print("  G:/youtube-uploader/data/temp/test_null_default.jpg")
    print("  G:/youtube-uploader/data/temp/test_explicit_text_only.jpg")
    print("  G:/youtube-uploader/data/temp/test_scene_frame_overlay_9_16.jpg")


if __name__ == "__main__":
    main()