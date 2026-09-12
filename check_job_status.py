#!/usr/bin/env python
"""Check queue and video job status."""
import sys
sys.path.insert(0, r'G:\youtube-uploader')

# Import in order to avoid SQLAlchemy registry issues
import backend.tts_models
import backend.content_models
import backend.video_generation_models
import backend.queue_models

from backend.db import SessionLocal
from backend.queue_models import ContentQueueJob
from backend.video_generation_models import VideoGenerationJob

db = SessionLocal()
try:
    jobs = db.query(ContentQueueJob).order_by(ContentQueueJob.created_at.desc()).limit(5).all()
    print("=== Recent Queue Jobs ===")
    for j in jobs:
        print(f"Job: {j.id[:8]}..., Status: {j.status}, Stage: {j.current_stage}, Progress: {j.progress}, VideoJobID: {j.video_job_id[:8] if j.video_job_id else None}..., Retry: {j.retry_count}/{j.max_retries}")
    
    vjobs = db.query(VideoGenerationJob).order_by(VideoGenerationJob.created_at.desc()).limit(5).all()
    print("\n=== Recent Video Jobs ===")
    for vj in vjobs:
        print(f"VideoJob: {vj.id[:8]}..., Status: {vj.status}, Progress: {vj.progress}, Step: {vj.current_step}, Output: {'Yes' if vj.output_path else 'No'}")
finally:
    db.close()
