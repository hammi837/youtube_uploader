import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import json
import uuid

from backend.main import app
from backend.services.video.media_utils import output_manifest_path, get_manifests_dir, get_temp_dir, cleanup_temp
from tests.conftest import SharedTestingSessionLocal, _override_get_db
from backend.queue_models import ContentQueueJob

app.dependency_overrides[__import__("backend.db").db.get_db] = _override_get_db
client = TestClient(app)

def _make_job():
    db = SharedTestingSessionLocal()
    job = ContentQueueJob(
        id=str(uuid.uuid4()),
        topic="test",
        status="completed"
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()
    return job_id

def test_manifest_persistence_and_cleanup():
    # Phase 3H: F. Persistence test (manifest survives cleanup_temp())
    job_id = _make_job()
    
    # 1. Create a dummy manifest
    manifest_dir = get_manifests_dir()
    manifest_path = output_manifest_path(job_id)
    manifest_data = {"job_id": job_id, "scenes": []}
    
    manifest_path.write_text(json.dumps(manifest_data))
    
    # Create temp dir to simulate pipeline
    temp_dir = get_temp_dir(job_id)
    (temp_dir / "some_temp_file.mp4").write_text("dummy")
    
    # 2. Run cleanup
    cleanup_temp(job_id)
    
    # 3. Verify
    assert not temp_dir.exists(), "Temp dir should be deleted"
    assert manifest_path.exists(), "Persistent manifest MUST survive cleanup"
    
    # Clean up manifest
    manifest_path.unlink()

def test_api_manifest_valid():
    # Phase 3H: E. API tests - valid manifest
    job_id = _make_job()
    
    manifest_path = output_manifest_path(job_id)
    manifest_data = {"job_id": job_id, "scene_count": 5, "final_sha256": "abc", "fallback_actions": []}
    manifest_path.write_text(json.dumps(manifest_data))
    
    response = client.get(f"/api/queue/{job_id}/manifest")
    assert response.status_code == 200
    assert response.json() == manifest_data
    
    manifest_path.unlink()

def test_api_manifest_missing():
    # Phase 3H: E. API tests - missing manifest
    job_id = _make_job()
    
    # Do not create the manifest file on disk
    response = client.get(f"/api/queue/{job_id}/manifest")
    assert response.status_code == 404
    assert "No manifest available" in response.json()["detail"]

def test_api_manifest_invalid_uuid():
    # Phase 3H: E. API tests - invalid UUID
    # Should be caught by the regex before hitting DB
    bad_id = "not-a-uuid"
    response = client.get(f"/api/queue/{bad_id}/manifest")
    assert response.status_code == 422
    assert "must be a valid UUID" in response.json()["detail"]

def test_api_manifest_malformed():
    # Phase 3H: E. API tests - malformed manifest
    job_id = _make_job()
    
    manifest_path = output_manifest_path(job_id)
    manifest_path.write_text("{ broken json")
    
    response = client.get(f"/api/queue/{job_id}/manifest")
    assert response.status_code == 500
    assert "could not be parsed" in response.json()["detail"]
    
    manifest_path.unlink()

def test_manifest_integrity_check():
    # 5. MANIFEST INTEGRITY TEST
    from backend.services.video.pipeline_models import RenderManifest, SceneManifestEntry, ProductionStage
    
    job_id = _make_job()
    
    manifest = RenderManifest(
        job_id=job_id,
        timestamp="2026-09-23T00:00:00Z",
        production_stage=ProductionStage.COMPLETED,
        aspect_ratio="16:9",
        width=1920,
        height=1080,
        scene_count=2,
        final_mp4_path="final.mp4",
        final_sha256="fake_sha",
        fallback_actions=["Audio fallback used"],
        scene_entries=[
            SceneManifestEntry(scene_number=1, duration_seconds=5.0, background_type=None, background_path=None, clip_path=None),
            SceneManifestEntry(scene_number=2, duration_seconds=5.0, background_type=None, background_path=None, clip_path=None)
        ]
    )
    
    manifest_path = output_manifest_path(job_id)
    manifest.save(manifest_path)
    
    # read back
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["job_id"] == job_id
    assert data["scene_count"] == 2
    assert data["final_mp4_path"] == "final.mp4"
    assert data["final_sha256"] == "fake_sha"
    assert "Audio fallback used" in data["fallback_actions"]
    
    manifest_path.unlink()
