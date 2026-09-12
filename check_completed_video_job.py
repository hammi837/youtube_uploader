from backend.db import SessionLocal
from backend.content_models import ContentProject
from backend.tts_models import GeneratedAudio
from backend.queue_models import ContentQueueJob
from backend.video_generation_models import VideoGenerationJob

db = SessionLocal()

# Check the successful video job from the logs
vj = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == 'af411d33-6ce9-4a95-bd56-b9e4c34d3470').first()

if vj:
    print(f'Video Job: {vj.id[:8]}...')
    print(f'  Status: {vj.status}')
    print(f'  Progress: {vj.progress}')
    print(f'  Step: {vj.current_step}')
    print(f'  Output: {vj.output_path}')
    print(f'  Thumbnail: {vj.thumbnail_path}')
    print(f'  Caption: {vj.caption_path}')
    print(f'  Duration: {vj.duration_seconds}')
    print(f'  File Size: {vj.file_size_bytes}')
    print(f'  Error: {vj.error_message}')
    print(f'  Completed At: {vj.completed_at}')
else:
    print('Video Job not found')

print('\n--- Queue Job ---')
job = db.query(ContentQueueJob).filter(ContentQueueJob.video_job_id == 'af411d33-6ce9-4a95-bd56-b9e4c34d3470').first()

if job:
    print(f'Queue Job: {job.id[:8]}...')
    print(f'  Status: {job.status}')
    print(f'  Stage: {job.current_stage}')
    print(f'  Progress: {job.progress}')
    print(f'  VideoJobID: {job.video_job_id[:8] if job.video_job_id else None}...')
    print(f'  YouTube Video ID: {job.youtube_video_id}')
    print(f'  YouTube URL: {job.youtube_url}')
    print(f'  Video Uploaded: {job.video_uploaded}')
    print(f'  Thumbnail Uploaded: {job.thumbnail_uploaded}')
    print(f'  Schedule Set: {job.schedule_set}')
    print(f'  Error: {job.error_message}')
else:
    print('Queue Job not found')

db.close()
