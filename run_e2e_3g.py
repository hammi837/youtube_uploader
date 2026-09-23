import os
import sys
import uuid
import time
import json
from pathlib import Path

# Add backend to path if needed
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.db import SessionLocal
from backend.queue_models import ContentQueueJob, QueueStatus
import backend.main  # Forces all models to load for SQLAlchemy registry
from backend.services.queue_processor import _process_job
from backend.services.video.media_utils import get_temp_dir

# Patch YouTube upload to just succeed so we don't block on auth
import backend.services.queue_processor as qp
qp._stage_youtube = lambda *args, **kwargs: print(f"Skipping YouTube upload for E2E test")

# Patch cleanup_temp so we can read the manifest and test idempotency
import backend.services.video.media_utils as media_utils
original_cleanup = media_utils.cleanup_temp
media_utils.cleanup_temp = lambda job_id, keep_on_failure=False: print(f"Skipping cleanup for {job_id}")


# Disable caption fallback warning copy so we can verify if SRT generation works
os.environ["CAPTIONS_FALLBACK_ENABLED"] = "false"

# 1. 16:9 AI E2E
job_16_9 = ContentQueueJob(
    id=str(uuid.uuid4()),
    topic="test 16:9 production Phase 3G",
    status=QueueStatus.QUEUED,
    aspect_ratio="16:9",
    background_type=None,
)

# 2. 9:16 AI E2E
job_9_16 = ContentQueueJob(
    id=str(uuid.uuid4()),
    topic="test 9:16 production Phase 3G",
    status=QueueStatus.QUEUED,
    aspect_ratio="9:16",
    background_type=None,
)

# 3. Short local video loop E2E
job_local = ContentQueueJob(
    id=str(uuid.uuid4()),
    topic="test short local video looping Phase 3G",
    status=QueueStatus.QUEUED,
    aspect_ratio="9:16",
    background_type="local_video",
    background_path="assets/backgrounds/videos/15341287_1080_1920_30fps.mp4",
)

db = SessionLocal()
db.add(job_16_9)
db.add(job_9_16)
db.add(job_local)
db.commit()

jobs = [
    ("16:9 Real E2E", job_16_9.id),
    ("9:16 Real E2E", job_9_16.id),
    ("Short Local Video E2E", job_local.id)
]
db.close()

results = {}

for test_name, jid in jobs:
    print(f"\n==============================================")
    print(f"RUNNING {test_name}: {jid}")
    print(f"==============================================\n")
    
    _process_job(jid)
    
    db = SessionLocal()
    j = db.query(ContentQueueJob).filter(ContentQueueJob.id == jid).first()
    
    # Phase 3H: manifests are stored by video_job_id, not queue job_id
    video_jid = j.video_job_id
    mf_path = media_utils.output_manifest_path(video_jid) if video_jid else media_utils.output_manifest_path(jid)
    manifest_data = None
    if mf_path.exists():
        manifest_data = json.loads(mf_path.read_text("utf-8"))
        
    results[test_name] = {
        "job_id": j.id,
        "video_job_id": video_jid,
        "production_stage": j.production_stage,
        "current_stage": j.current_stage,
        "summary": j.summary,
        "status": j.status,
        "manifest_sha256": manifest_data.get("final_sha256") if manifest_data else None,
        "manifest_scene_count": manifest_data.get("scene_count") if manifest_data else None,
        "manifest_ok": manifest_data is not None,
    }
    db.close()

# Artifact Idempotency Test
print(f"\n==============================================")
print(f"RUNNING Artifact Idempotency Check")
print(f"==============================================\n")

idem_job = ContentQueueJob(
    id=str(uuid.uuid4()),
    topic="test idempotency Phase 3G",
    status=QueueStatus.QUEUED,
    aspect_ratio="16:9",
)
db = SessionLocal()
db.add(idem_job)
db.commit()
jid_idem = idem_job.id
db.close()

# Run it once
_process_job(jid_idem)

# Now, rerun on the same job with the temp dir still intact
db = SessionLocal()
j = db.query(ContentQueueJob).filter(ContentQueueJob.id == jid_idem).first()
# Reset status so it runs again
j.status = QueueStatus.QUEUED
# Remove video_job_id so queue_processor creates a new video job, which will call pipeline again on the same job_id (wait, pipeline uses job_id for temp_dir)
j.video_job_id = None
db.commit()
db.close()

print(f"\n--- Rerunning job for idempotency check (check logs for 'Reusing existing') ---")
_process_job(jid_idem)

print("\n--- RESULTS ---")
print(json.dumps(results, indent=2))

