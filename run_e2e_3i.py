import time
import uuid
import json
from pathlib import Path
from unittest.mock import patch
import backend.main
from backend.db import SessionLocal
from backend.queue_models import ContentQueueJob, QueueStatus
from backend.services.queue_processor import _process_job
from backend.services.video.media_utils import output_manifest_path

def run_job(label, aspect_ratio, tts_voice, music_style, background_type="gradient"):
    print(f"\n{'='*50}\nStarting E2E Job: {label}\n{'='*50}")
    db = SessionLocal()
    job_id = str(uuid.uuid4())
    job = ContentQueueJob(
        id=job_id,
        topic=f"E2E test: {label}",
        language="en",
        tone="engaging",
        target_duration_seconds=30, # pydantic requires >= 30
        scene_count=3,              # pydantic requires >= 3
        priority=0,
        status=QueueStatus.QUEUED,
        aspect_ratio=aspect_ratio,
        background_type=background_type,
        tts_voice=tts_voice,
        music_style=music_style
    )
    db.add(job)
    db.commit()
    db.close()
    
    t0 = time.time()
    _process_job(job_id)
    t1 = time.time()
    
    db = SessionLocal()
    job = db.query(ContentQueueJob).filter(ContentQueueJob.id == job_id).first()
    print(f"Status: {job.status}")
    print(f"Summary: {job.summary}")
    print(f"Time: {t1 - t0:.1f}s")
    
    manifest_path = output_manifest_path(job.video_job_id) if job.video_job_id else None
    if manifest_path and manifest_path.exists():
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
            print("Manifest fallbacks:", manifest.get("fallbacks"))
    else:
        print("No manifest found!")
        
    db.close()

if __name__ == "__main__":
    # 1. 16:9 custom voice + music style
    run_job("E2E 1: 16:9 custom voice & style v2", "16:9", "en-GB-SoniaNeural", "lofi")
