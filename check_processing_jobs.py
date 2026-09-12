#!/usr/bin/env python
"""Check processing jobs."""
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
    # Get all jobs in processing state
    jobs = db.query(ContentQueueJob).filter(ContentQueueJob.status == 'processing').all()
    print(f"=== Processing Jobs: {len(jobs)} ===")
    for j in jobs:
        print(f"\nJob: {j.id}")
        print(f"Status: {j.status}")
        print(f"Stage: {j.current_stage}")
        print(f"Progress: {j.progress}")
        print(f"VideoJobID: {j.video_job_id}")
        print(f"Retry: {j.retry_count}/{j.max_retries}")
        print(f"Error: {j.error_message}")
        print(f"Started: {j.started_at}")
        print(f"Updated: {j.updated_at}")
        
        if j.video_job_id:
            vj = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == j.video_job_id).first()
            if vj:
                print(f"\n  Video Job: {vj.id}")
                print(f"  Status: {vj.status}")
                print(f"  Progress: {vj.progress}")
                print(f"  Step: {vj.current_step}")
                print(f"  Output: {vj.output_path}")
                print(f"  Completed: {vj.completed_at}")
finally:
    db.close()
