#!/usr/bin/env python
"""Check specific job status."""
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
    # Get the most recent job
    job = db.query(ContentQueueJob).order_by(ContentQueueJob.created_at.desc()).first()
    if job:
        print(f"=== Most Recent Queue Job ===")
        print(f"ID: {job.id}")
        print(f"Status: {job.status}")
        print(f"Stage: {job.current_stage}")
        print(f"Progress: {job.progress}")
        print(f"VideoJobID: {job.video_job_id}")
        print(f"Retry: {job.retry_count}/{job.max_retries}")
        print(f"Error: {job.error_message}")
        print(f"Started: {job.started_at}")
        print(f"Completed: {job.completed_at}")
        print(f"Updated: {job.updated_at}")
        
        if job.video_job_id:
            vj = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job.video_job_id).first()
            if vj:
                print(f"\n=== Video Job ===")
                print(f"ID: {vj.id}")
                print(f"Status: {vj.status}")
                print(f"Progress: {vj.progress}")
                print(f"Step: {vj.current_step}")
                print(f"Output: {vj.output_path}")
                print(f"Completed: {vj.completed_at}")
finally:
    db.close()
