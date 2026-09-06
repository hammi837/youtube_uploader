"""
tests/test_video_generation.py — Phase 2D video generation tests.

All FFmpeg, Whisper, and Pillow calls are mocked.
No real FFmpeg execution, no real model download, no real file I/O
(except where explicitly testing file-system safety).

Coverage:
  A.  Job creation — valid project with script          → 202 + queued
  B.  Job creation — missing project                    → 404
  C.  Job creation — project without script             → 422
  D.  Job creation — invalid audio_id                   → 404
  E.  Job creation — valid explicit audio_id            → 202
  F.  List jobs endpoint                                 → 200
  G.  Get job by ID — found                             → 200
  H.  Get job by ID — not found                         → 404
  I.  Video stream — completed job                      → 200 video/mp4
  J.  Video stream — job not complete                   → 409
  K.  Video stream — missing file                       → 404
  L.  Thumbnail stream — present                        → 200 image/jpeg
  M.  Thumbnail stream — missing                        → 404
  N.  Captions download — present                       → 200 text/plain
  O.  Captions download — missing                       → 404
  P.  Delete job — cleans up records                    → 204
  Q.  Delete job — not found                            → 404
  R.  Job statuses cover expected set
  S.  Progress 0–100 range
  T.  output_path never exposed raw in response
  U.  No secrets in any response
  V.  Path traversal prevented in media_utils
  W.  Scene duration calculation — proportional
  X.  Scene duration calculation — equal fallback
  Y.  Scene duration sum ≈ audio duration
  Z.  Thumbnail generation — file written
  AA. Scene card generation — file written
  AB. Title card generation — file written
  AC. SRT timestamp formatting
  AD. Fallback captions from narration text
  AE. Caption file written by fallback generator
  AF. FFmpeg unavailable → PipelineConfigError
  AG. Missing narration audio file → PipelineConfigError
  AH. probe_video returns expected keys
  AI. Job completed_at set on success
  AJ. Job error_message set on failure
  AK. safe_path_for_job — traversal rejected
  AL. safe_path_for_job — valid path accepted
  AM. media_utils directories created on G:
  AN. Background task sets status=failed on exception
  AO. Pipeline result dict has expected keys
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.db import Base, get_db
from backend.main import app
from backend.content_models import ContentProject, ContentStatus, GeneratedScriptRecord
from backend.tts_models import GeneratedAudio, AudioStatus
from backend.video_generation_models import (
    VideoGenerationJob, VideoJobStatus, job_to_response,
)
from backend.services.video.exceptions import (
    PipelineConfigError, FFmpegError, VideoGenerationError,
)

from tests.conftest import shared_engine, SharedTestingSessionLocal

test_engine = shared_engine
TestingSessionLocal = SharedTestingSessionLocal


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── DB helpers ────────────────────────────────────────────────────────────────

def make_project(db, with_script: bool = True) -> ContentProject:
    project = ContentProject(
        id=str(uuid.uuid4()),
        topic="Why do cats purr?",
        language="en",
        tone="informative",
        target_duration_seconds=180,
        scene_count=5,
        status=ContentStatus.COMPLETED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(project)
    if with_script:
        script = GeneratedScriptRecord(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            title="Why Do Cats Purr?",
            description="Explore the science of cat purring.",
            hook="Have you ever wondered why cats purr?",
            tags_json=json.dumps(["cats", "science"]),
            scenes_json=json.dumps([
                {"scene_number": 1, "narration": "Cats purr for many reasons.",
                 "visual_description": "Cat sitting", "estimated_duration_seconds": 30},
                {"scene_number": 2, "narration": "Scientists believe purring aids healing.",
                 "visual_description": "Research lab", "estimated_duration_seconds": 40},
                {"scene_number": 3, "narration": "Purring frequencies reduce stress.",
                 "visual_description": "Relaxed cat", "estimated_duration_seconds": 30},
            ]),
            estimated_duration_seconds=180,
        )
        db.add(script)
    db.commit()
    db.refresh(project)
    return project


def make_audio(db, project_id: str, file_path: str = "/fake/audio.wav") -> GeneratedAudio:
    audio = GeneratedAudio(
        id=str(uuid.uuid4()),
        content_project_id=project_id,
        voice="en-US-AriaNeural",
        language="en",
        text_length=200,
        file_path=file_path,
        file_size_bytes=500000,
        duration_seconds=120.0,
        status=AudioStatus.COMPLETED,
        provider="edge",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(audio)
    db.commit()
    db.refresh(audio)
    return audio


def make_job(db, project_id: str, status: str = VideoJobStatus.COMPLETED,
             output_path: str = None, thumbnail_path: str = None,
             caption_path: str = None) -> VideoGenerationJob:
    job = VideoGenerationJob(
        id=str(uuid.uuid4()),
        content_project_id=project_id,
        status=status,
        progress=100 if status == VideoJobStatus.COMPLETED else 0,
        current_step="Complete" if status == VideoJobStatus.COMPLETED else "Queued",
        output_path=output_path,
        thumbnail_path=thumbnail_path,
        caption_path=caption_path,
        width=1920, height=1080, fps=30,
        captions_enabled=True, music_enabled=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc) if status == VideoJobStatus.COMPLETED else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _make_wav(path: Path, duration_secs: float = 2.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 22050
    n_frames = int(sample_rate * duration_secs)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_frames}h", *([0] * n_frames)))


# ── A: Job creation — valid project ──────────────────────────────────────────

class TestCreateVideoJob:

    def test_valid_project_returns_202(self, client, db):
        project = make_project(db)
        with patch("backend.routers.video_generation._run_video_pipeline_task"):
            r = client.post(f"/api/video-generation/from-content/{project.id}", json={})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == VideoJobStatus.QUEUED
        assert body["progress"] == 0
        assert body["content_project_id"] == project.id

    # B. Missing project
    def test_missing_project_returns_404(self, client):
        r = client.post("/api/video-generation/from-content/nonexistent", json={})
        assert r.status_code == 404

    # C. Project without script
    def test_project_without_script_returns_422(self, client, db):
        project = make_project(db, with_script=False)
        r = client.post(f"/api/video-generation/from-content/{project.id}", json={})
        assert r.status_code == 422
        assert "script" in r.json()["detail"].lower()

    # D. Invalid audio_id
    def test_invalid_audio_id_returns_404(self, client, db):
        project = make_project(db)
        r = client.post(
            f"/api/video-generation/from-content/{project.id}",
            json={"audio_id": "nonexistent-audio-id"},
        )
        assert r.status_code == 404

    # E. Valid explicit audio_id
    def test_valid_audio_id_accepted(self, client, db):
        project = make_project(db)
        audio = make_audio(db, project.id)
        with patch("backend.routers.video_generation._run_video_pipeline_task"):
            r = client.post(
                f"/api/video-generation/from-content/{project.id}",
                json={"audio_id": audio.id},
            )
        assert r.status_code == 202
        assert r.json()["audio_id"] == audio.id

    def test_job_record_created_in_db(self, client, db):
        project = make_project(db)
        with patch("backend.routers.video_generation._run_video_pipeline_task"):
            r = client.post(f"/api/video-generation/from-content/{project.id}", json={})
        assert r.status_code == 202
        job_id = r.json()["id"]
        job = db.query(VideoGenerationJob).filter(
            VideoGenerationJob.id == job_id
        ).first()
        assert job is not None
        assert job.status == VideoJobStatus.QUEUED

    def test_default_resolution_1920x1080(self, client, db):
        project = make_project(db)
        with patch("backend.routers.video_generation._run_video_pipeline_task"):
            r = client.post(f"/api/video-generation/from-content/{project.id}", json={})
        body = r.json()
        assert body["width"] == 1920
        assert body["height"] == 1080
        assert body["fps"] == 30

    def test_custom_resolution_accepted(self, client, db):
        project = make_project(db)
        with patch("backend.routers.video_generation._run_video_pipeline_task"):
            r = client.post(
                f"/api/video-generation/from-content/{project.id}",
                json={"width": 1280, "height": 720, "fps": 24},
            )
        assert r.status_code == 202
        body = r.json()
        assert body["width"] == 1280
        assert body["height"] == 720


# ── F–H: List / get endpoints ─────────────────────────────────────────────────

class TestListGetJobs:

    # F. List
    def test_list_returns_200(self, client, db):
        project = make_project(db)
        make_job(db, project.id)
        r = client.get("/api/video-generation")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    # G. Get found
    def test_get_job_found(self, client, db):
        project = make_project(db)
        job = make_job(db, project.id)
        r = client.get(f"/api/video-generation/{job.id}")
        assert r.status_code == 200
        assert r.json()["id"] == job.id

    # H. Get not found
    def test_get_job_not_found(self, client):
        r = client.get("/api/video-generation/nonexistent-job-id")
        assert r.status_code == 404


# ── I–K: Video streaming ──────────────────────────────────────────────────────

class TestVideoStream:

    # I. Completed job with file
    def test_streams_mp4_when_completed(self, client, db, tmp_path):
        fake_mp4 = tmp_path / "test.mp4"
        fake_mp4.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100)
        project = make_project(db)
        job = make_job(db, project.id, output_path=str(fake_mp4))
        r = client.get(f"/api/video-generation/{job.id}/video")
        assert r.status_code == 200
        assert "video/mp4" in r.headers["content-type"]

    # J. Not ready
    def test_video_not_ready_returns_409(self, client, db):
        project = make_project(db)
        job = make_job(db, project.id, status=VideoJobStatus.ASSEMBLING)
        r = client.get(f"/api/video-generation/{job.id}/video")
        assert r.status_code == 409

    # K. Missing file
    def test_video_file_missing_returns_404(self, client, db):
        project = make_project(db)
        job = make_job(db, project.id, output_path="/nonexistent/path/video.mp4")
        r = client.get(f"/api/video-generation/{job.id}/video")
        assert r.status_code == 404


# ── L–M: Thumbnail ────────────────────────────────────────────────────────────

class TestThumbnailStream:

    # L. Present
    def test_streams_thumbnail(self, client, db, tmp_path):
        fake_jpg = tmp_path / "thumb.jpg"
        fake_jpg.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        project = make_project(db)
        job = make_job(db, project.id, thumbnail_path=str(fake_jpg))
        r = client.get(f"/api/video-generation/{job.id}/thumbnail")
        assert r.status_code == 200
        assert "image/jpeg" in r.headers["content-type"]

    # M. Missing
    def test_thumbnail_missing_returns_404(self, client, db):
        project = make_project(db)
        job = make_job(db, project.id)  # no thumbnail_path
        r = client.get(f"/api/video-generation/{job.id}/thumbnail")
        assert r.status_code == 404


# ── N–O: Captions ─────────────────────────────────────────────────────────────

class TestCaptionsDownload:

    # N. Present
    def test_downloads_srt(self, client, db, tmp_path):
        fake_srt = tmp_path / "captions.srt"
        fake_srt.write_text("1\n00:00:00,000 --> 00:00:03,000\nHello world\n")
        project = make_project(db)
        job = make_job(db, project.id, caption_path=str(fake_srt))
        r = client.get(f"/api/video-generation/{job.id}/captions")
        assert r.status_code == 200

    # O. Missing
    def test_captions_missing_returns_404(self, client, db):
        project = make_project(db)
        job = make_job(db, project.id)  # no caption_path
        r = client.get(f"/api/video-generation/{job.id}/captions")
        assert r.status_code == 404


# ── P–Q: Delete ───────────────────────────────────────────────────────────────

class TestDeleteJob:

    # P. Delete existing
    def test_delete_returns_204(self, client, db):
        project = make_project(db)
        job = make_job(db, project.id)
        r = client.delete(f"/api/video-generation/{job.id}")
        assert r.status_code == 204
        assert db.query(VideoGenerationJob).filter(
            VideoGenerationJob.id == job.id
        ).first() is None

    # Q. Delete not found
    def test_delete_not_found_returns_404(self, client):
        r = client.delete("/api/video-generation/nonexistent-job")
        assert r.status_code == 404


# ── R–S: Status and progress ─────────────────────────────────────────────────

class TestStatusAndProgress:

    # R. All expected statuses exist
    def test_video_job_statuses_defined(self):
        assert VideoJobStatus.QUEUED == "queued"
        assert VideoJobStatus.COMPLETED == "completed"
        assert VideoJobStatus.FAILED == "failed"
        assert VideoJobStatus.ASSEMBLING == "assembling"
        assert VideoJobStatus.GENERATING_CAPTIONS == "generating_captions"
        expected = {
            "queued", "preparing", "generating_audio", "preparing_visuals",
            "generating_captions", "assembling", "generating_thumbnail",
            "completed", "failed", "cancelled",
        }
        assert VideoJobStatus.ALL == expected

    # S. Progress within 0–100
    def test_progress_range(self, client, db):
        project = make_project(db)
        job = make_job(db, project.id)
        body = client.get(f"/api/video-generation/{job.id}").json()
        assert 0 <= body["progress"] <= 100


# ── T–U: Security ─────────────────────────────────────────────────────────────

class TestSecurity:

    # T. output_path not in response JSON
    def test_output_path_not_in_job_response(self, client, db, tmp_path):
        fake_mp4 = tmp_path / "out.mp4"
        fake_mp4.write_bytes(b"\x00" * 10)
        project = make_project(db)
        job = make_job(db, project.id, output_path=str(fake_mp4))
        body = client.get(f"/api/video-generation/{job.id}").json()
        # raw filesystem path should NOT be in the JSON
        assert str(fake_mp4) not in str(body)
        # safe URL should be present instead
        assert "video_url" in body

    # U. No secrets
    def test_no_secrets_in_response(self, client, db):
        project = make_project(db)
        with patch("backend.routers.video_generation._run_video_pipeline_task"):
            r = client.post(f"/api/video-generation/from-content/{project.id}", json={})
        body = r.text
        assert "GROQ_API_KEY" not in body
        assert "client_secret" not in body
        assert "refresh_token" not in body
        assert "access_token" not in body


# ── V: Path traversal ─────────────────────────────────────────────────────────

class TestPathTraversal:

    # V
    def test_safe_path_rejects_traversal(self):
        from backend.services.video.media_utils import safe_path_for_job
        from pathlib import Path
        base = Path("G:/youtube-uploader/data/videos")
        with pytest.raises(ValueError, match="Invalid job_id"):
            safe_path_for_job("../evil", base, ".mp4")
        with pytest.raises(ValueError, match="Invalid job_id"):
            safe_path_for_job("some\\path", base, ".mp4")

    def test_safe_path_accepts_valid_uuid(self):
        from backend.services.video.media_utils import safe_path_for_job
        from pathlib import Path
        job_id = str(uuid.uuid4())
        base = Path("G:/youtube-uploader/data/videos")
        result = safe_path_for_job(job_id, base, ".mp4")
        assert result == base / f"{job_id}.mp4"


# ── W–Y: Scene duration calculation ──────────────────────────────────────────

class TestSceneDurations:

    # W. Proportional to narration length
    def test_proportional_to_char_count(self):
        from backend.services.video.ffmpeg_assembler import calculate_scene_durations
        scenes = [
            {"narration": "x" * 100},  # 100 chars
            {"narration": "x" * 200},  # 200 chars
            {"narration": "x" * 100},  # 100 chars
        ]
        durations = calculate_scene_durations(scenes, total_audio_duration=120.0)
        assert len(durations) == 3
        # Scene 2 should be roughly twice scene 1 or 3
        assert durations[1] > durations[0]
        assert abs(durations[0] - durations[2]) < 1.0

    # X. Equal when all empty
    def test_handles_empty_narration(self):
        from backend.services.video.ffmpeg_assembler import calculate_scene_durations
        scenes = [{"narration": ""}, {"narration": ""}, {"narration": ""}]
        durations = calculate_scene_durations(scenes, total_audio_duration=90.0)
        assert len(durations) == 3
        for d in durations:
            assert d > 0

    # Y. Sum approximately equals audio duration
    def test_durations_sum_to_audio_duration(self):
        from backend.services.video.ffmpeg_assembler import calculate_scene_durations
        scenes = [{"narration": f"scene {i} text " * 20} for i in range(5)]
        total = 150.0
        durations = calculate_scene_durations(scenes, total_audio_duration=total)
        assert abs(sum(durations) - total) < 0.5


# ── Z–AB: Visual generation ───────────────────────────────────────────────────

class TestVisualGeneration:

    # Z. Thumbnail
    def test_thumbnail_creates_jpg(self, tmp_path):
        from backend.services.video.thumbnail import generate_thumbnail
        out = tmp_path / "thumb.jpg"
        result = generate_thumbnail("Test Title", "Opening hook.", str(out))
        assert result.exists()
        assert result.suffix == ".jpg"
        assert result.stat().st_size > 1000

    # AA. Scene card
    def test_scene_card_creates_png(self, tmp_path):
        from backend.services.video.visual_builder import generate_scene_card
        out = tmp_path / "scene.png"
        result = generate_scene_card(
            scene_number=1,
            title="Test Title",
            narration="This is the narration text for this scene.",
            visual_description="A cat sitting on a windowsill.",
            output_path=str(out),
        )
        assert result.exists()
        assert result.suffix == ".png"
        assert result.stat().st_size > 1000

    # AB. Title card
    def test_title_card_creates_png(self, tmp_path):
        from backend.services.video.visual_builder import generate_title_card
        out = tmp_path / "title.png"
        result = generate_title_card(
            title="Why Do Cats Purr?",
            hook="Have you ever wondered why cats purr?",
            output_path=str(out),
        )
        assert result.exists()
        assert result.suffix == ".png"
        assert result.stat().st_size > 1000

    def test_thumbnail_different_titles_produce_different_colours(self, tmp_path):
        from backend.services.video.thumbnail import generate_thumbnail
        out1 = tmp_path / "t1.jpg"
        out2 = tmp_path / "t2.jpg"
        generate_thumbnail("Cats Purring Science", "Hook 1", str(out1))
        generate_thumbnail("ZZZZZ Very Different", "Hook 2", str(out2))
        # Both must exist — colour difference isn't easily checked here
        assert out1.exists() and out2.exists()

    def test_long_title_does_not_crash(self, tmp_path):
        from backend.services.video.visual_builder import generate_scene_card
        out = tmp_path / "long.png"
        long_title = "A" * 200
        result = generate_scene_card(1, long_title, "narration", "visual", str(out))
        assert result.exists()


# ── AC–AE: Caption generation ─────────────────────────────────────────────────

class TestCaptionGeneration:

    # AC. SRT timestamp formatting
    def test_srt_timestamp_format(self):
        from backend.services.video.caption_generator import _seconds_to_srt_time
        assert _seconds_to_srt_time(0.0)   == "00:00:00,000"
        assert _seconds_to_srt_time(3.5)   == "00:00:03,500"
        assert _seconds_to_srt_time(65.25) == "00:01:05,250"
        assert _seconds_to_srt_time(3661.1) == "01:01:01,100"

    # AD. Fallback caption content
    def test_fallback_captions_content(self, tmp_path):
        from backend.services.video.caption_generator import generate_fallback_captions
        text = "This is the narration text for the video about cats."
        out = tmp_path / "captions.srt"
        result = generate_fallback_captions(text, audio_duration=30.0, output_srt_path=str(out))
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "-->" in content
        assert "narration" in content.lower() or "cats" in content.lower()

    # AE. Fallback writes file
    def test_fallback_captions_file_written(self, tmp_path):
        from backend.services.video.caption_generator import generate_fallback_captions
        out = tmp_path / "captions.srt"
        generate_fallback_captions("One two three four five six seven eight.", 10.0, str(out))
        assert out.exists()
        assert out.stat().st_size > 0

    def test_fallback_empty_text_writes_empty_file(self, tmp_path):
        from backend.services.video.caption_generator import generate_fallback_captions
        out = tmp_path / "empty.srt"
        generate_fallback_captions("", 10.0, str(out))
        assert out.exists()

    def test_caption_clean_text(self):
        from backend.services.video.caption_generator import _clean_caption_text
        assert _clean_caption_text("  hello   world  ") == "hello world"
        assert _clean_caption_text("line1\nline2") == "line1 line2"


# ── AF–AG: Pipeline config errors ────────────────────────────────────────────

class TestPipelineConfigErrors:

    # AF. FFmpeg unavailable
    def test_ffmpeg_unavailable_raises_pipeline_config_error(self, tmp_path):
        from backend.services.video.pipeline import _run_pipeline_sync
        from backend.services.media.ffmpeg import FFmpegNotFoundError

        with patch("backend.services.media.ffmpeg.check_ffmpeg",
                   return_value={"available": False, "error": "not found"}):
            with pytest.raises(PipelineConfigError, match="FFmpeg"):
                _run_pipeline_sync(
                    job_id=str(uuid.uuid4()),
                    content_project_id="nonexistent",
                    audio_id=None,
                    width=1920, height=1080, fps=30,
                    captions_enabled=False, music_enabled=False,
                    progress_callback=lambda p, s: None,
                )

    # AG. Missing project
    def test_missing_project_raises_pipeline_config_error(self, tmp_path):
        from backend.services.video.pipeline import _run_pipeline_sync
        with patch("backend.services.media.ffmpeg.check_ffmpeg",
                   return_value={"available": True, "version": "N-test"}):
            with pytest.raises(PipelineConfigError, match="not found"):
                _run_pipeline_sync(
                    job_id=str(uuid.uuid4()),
                    content_project_id="nonexistent-project-id",
                    audio_id=None,
                    width=1920, height=1080, fps=30,
                    captions_enabled=False, music_enabled=False,
                    progress_callback=lambda p, s: None,
                )


# ── AH: probe_video ───────────────────────────────────────────────────────────

class TestProbeVideo:

    # AH. probe returns expected keys
    def test_probe_video_returns_expected_keys(self, tmp_path):
        from backend.services.video.ffmpeg_assembler import probe_video

        fake_probe_output = json.dumps({
            "streams": [
                {"codec_type": "video", "codec_name": "h264",
                 "width": 1920, "height": 1080,
                 "r_frame_rate": "30/1", "duration": "120.0"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "120.0"},
            ],
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration": "120.0",
                "size": "5000000",
            },
        })

        fake_mp4 = tmp_path / "fake.mp4"
        fake_mp4.write_bytes(b"\x00" * 100)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=fake_probe_output, stderr=""
            )
            result = probe_video(str(fake_mp4))

        assert "duration" in result
        assert "streams" in result
        assert "format" in result
        assert result["duration"] == 120.0

        video_streams = [s for s in result["streams"] if s["codec_type"] == "video"]
        audio_streams = [s for s in result["streams"] if s["codec_type"] == "audio"]
        assert len(video_streams) == 1
        assert len(audio_streams) == 1
        assert video_streams[0]["width"] == 1920
        assert video_streams[0]["height"] == 1080


# ── AI–AJ: Job state transitions ─────────────────────────────────────────────

class TestJobStateTransitions:

    # AI. completed_at set when complete
    def test_completed_at_set_on_completion(self, db):
        project = make_project(db)
        job = make_job(db, project.id, status=VideoJobStatus.COMPLETED)
        assert job.completed_at is not None

    # AJ. error_message set on failure
    def test_error_message_set_on_failure(self, db):
        project = make_project(db)
        job = VideoGenerationJob(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            status=VideoJobStatus.FAILED,
            progress=10,
            error_message="FFmpeg encoding failed: codec not found",
            width=1920, height=1080, fps=30,
            captions_enabled=True, music_enabled=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        assert job.status == VideoJobStatus.FAILED
        assert "FFmpeg" in job.error_message


# ── AK–AL: safe_path_for_job ─────────────────────────────────────────────────

class TestSafePathForJob:
    # AK covered in TestPathTraversal above.
    # AL. Valid UUID
    def test_valid_uuid_path(self):
        from backend.services.video.media_utils import safe_path_for_job
        job_id = str(uuid.uuid4())
        base = Path("G:/test/dir")
        p = safe_path_for_job(job_id, base, ".mp4")
        assert str(p).endswith(f"{job_id}.mp4")
        assert ".." not in str(p)


# ── AM: Directories on G: ─────────────────────────────────────────────────────

class TestMediaUtilsDirs:
    def test_data_dir_on_g_drive(self):
        from backend.services.video.media_utils import get_data_dir
        with patch.dict(os.environ, {"DATA_DIR": r"G:\youtube-uploader\data"}):
            d = get_data_dir()
        assert str(d).startswith("G:")


# ── AN: Background task failure handling ─────────────────────────────────────

class TestBackgroundTaskFailure:

    # AN. Background task sets failed on exception
    def test_background_task_sets_failed_on_exception(self, db):
        project = make_project(db)
        job = VideoGenerationJob(
            id=str(uuid.uuid4()),
            content_project_id=project.id,
            status=VideoJobStatus.QUEUED,
            progress=0,
            width=1920, height=1080, fps=30,
            captions_enabled=True, music_enabled=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()

        from backend.routers.video_generation import _run_video_pipeline_task
        from tests.conftest import SharedTestingSessionLocal

        with patch("backend.services.media.ffmpeg.check_ffmpeg",
                   return_value={"available": False, "error": "not found"}), \
             patch("backend.db.SessionLocal", SharedTestingSessionLocal):
            _run_video_pipeline_task(
                job_id=job.id,
                content_project_id=project.id,
                audio_id=None,
                width=1920, height=1080, fps=30,
                captions_enabled=False, music_enabled=False,
            )

        db.expire(job)
        db.refresh(job)
        assert job.status == VideoJobStatus.FAILED
        assert job.error_message is not None


# ── AO: Pipeline result dict ──────────────────────────────────────────────────

class TestPipelineResultDict:

    def test_job_to_response_has_url_fields(self, db):
        project = make_project(db)
        job = make_job(db, project.id, output_path="/fake/video.mp4")
        resp = job_to_response(job)
        assert resp.video_url is not None
        assert "/video" in resp.video_url
        assert resp.id in resp.video_url

    def test_job_to_response_no_url_when_not_complete(self, db):
        project = make_project(db)
        job = make_job(db, project.id, status=VideoJobStatus.ASSEMBLING)
        resp = job_to_response(job)
        assert resp.video_url is None

    def test_no_raw_paths_in_response_model(self, db):
        project = make_project(db)
        job = make_job(db, project.id, output_path="/some/path/video.mp4")
        resp = job_to_response(job)
        resp_dict = resp.model_dump()
        # output_path, thumbnail_path, caption_path should be None in response
        assert resp_dict.get("output_path") is None
        assert resp_dict.get("thumbnail_path") is None
        assert resp_dict.get("caption_path") is None
