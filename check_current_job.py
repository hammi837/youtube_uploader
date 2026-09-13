from backend.db import SessionLocal
from backend.content_models import ContentProject
from backend.tts_models import GeneratedAudio
from backend.queue_models import ContentQueueJob
from backend.video_generation_models import VideoGenerationJob

db = SessionLocal()
job = db.query(ContentQueueJob).filter(ContentQueueJob.id.like('64df9503%')).first()

if job:
    print(f'Queue Job: {job.id[:8]}...')
    print(f'  Status: {job.status}')
    print(f'  Stage: {job.current_stage}')
    print(f'  Progress: {job.progress}')
    print(f'  VideoJobID: {job.video_job_id[:8] if job.video_job_id else None}...')
    print(f'  Started: {job.started_at}')
    print(f'  Updated: {job.updated_at}')
    print(f'  Retry: {job.retry_count}/{job.max_retries}')
    print(f'  Error: {job.error_message}')

    if job.video_job_id:
        vj = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job.video_job_id).first()
        if vj:
            print(f'\nVideo Job: {vj.id[:8]}...')
            print(f'  Status: {vj.status}')
            print(f'  Progress: {vj.progress}')
            print(f'  Step: {vj.current_step}')
            print(f'  Output: {vj.output_path}')
            print(f'  Error: {vj.error_message}')
            print(f'  Updated: {vj.updated_at}')
        else:
            print('Video Job not found')
    else:
        print('No VideoJobID')
else:
    print('Queue Job not found')

db.close()
