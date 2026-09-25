"""
run_e2e_3j.py — Phase 3J E2E test script.
"""

import sys
import time
import requests
from pathlib import Path
from datetime import datetime, timezone

BASE_URL = "http://localhost:8000"


def main():
    print("=" * 60)
    print("Phase 3J E2E Test Suite")
    print("=" * 60)
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"API URL: {BASE_URL}")
    
    # Check backend
    try:
        resp = requests.get(f"{BASE_URL}/api/health", timeout=5)
        resp.raise_for_status()
        print("Backend is running")
    except Exception as e:
        print(f"Backend not running: {e}")
        return
    
    # Resume queue
    print("\nResuming queue...")
    requests.post(f"{BASE_URL}/api/queue/resume", timeout=10)
    time.sleep(2)
    
    # E2E-1: 16:9 + scene_frame
    print("\n" + "=" * 60)
    print("E2E-1: 16:9 + scene_frame")
    print("=" * 60)
    
    timestamp = str(time.time())
    payload = {
        "topics": [f"E2E-1 16:9 scene_frame {timestamp}"],
        "language": "en",
        "tone": "engaging",
        "target_duration_seconds": 30,
        "scene_count": 3,
        "aspect_ratio": "16:9",
        "thumbnail_style": "scene_frame"
    }
    
    resp = requests.post(f"{BASE_URL}/api/queue", json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    job_id = data["jobs"][0]["id"]
    print(f"Created job: {job_id}")
    print(f"Thumbnail style: {data['jobs'][0]['thumbnail_style']}")
    
    # Wait for completion
    print(f"Waiting for job {job_id}...")
    for i in range(90):  # 15 minutes max
        job = requests.get(f"{BASE_URL}/api/queue/{job_id}", timeout=30).json()
        print(f"  Status: {job['status']} | Progress: {job['progress']}% | Stage: {job.get('current_stage', 'N/A')}")
        if job['status'] in ('completed', 'failed', 'cancelled'):
            break
        time.sleep(10)
    else:
        print("Timeout waiting for job")
        return
    
    print(f"\nFinal status: {job['status']}")
    print(f"Summary: {job.get('summary', 'N/A')}")
    print(f"Video job ID: {job.get('video_job_id', 'N/A')}")
    
    if job['status'] == 'completed' and job.get('video_job_id'):
        sys.path.insert(0, str(Path(__file__).parent))
        from backend.services.video.media_utils import output_thumbnail_path
        thumb_path = output_thumbnail_path(job['video_job_id'])
        
        print(f"\nThumbnail Verification:")
        print(f"  Path: {thumb_path}")
        print(f"  Exists: {thumb_path.exists()}")
        
        if thumb_path.exists():
            from PIL import Image
            img = Image.open(thumb_path)
            print(f"  Dimensions: {img.size}")
            print(f"  Format: {img.format}")
            print(f"  Size: {thumb_path.stat().st_size} bytes")
            img.close()
            
            print(f"\n[PENDING] Manual visual inspection required:")
            print(f"  File: {thumb_path}")
            print("  Check: frame visible, text readable, no severe crop, no black/blank")
        else:
            print("Thumbnail file not found")
    else:
        print("Job did not complete successfully")
        print(f"Error: {job.get('error_message', 'N/A')}")


if __name__ == "__main__":
    main()