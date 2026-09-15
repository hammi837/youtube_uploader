# YouTube Uploader Codebase Guide

**Purpose:** Comprehensive guide for AI agents and developers working on this codebase.

**Last Updated:** 2026-09-14  
**Phase:** Phase 3E.1 (Template Foundation) complete, Phase 3E.2 pending

---

## 📋 Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Technology Stack](#technology-stack)
3. [Directory Structure](#directory-structure)
4. [Core Systems](#core-systems)
5. [Database Schema](#database-schema)
6. [API Endpoints](#api-endpoints)
7. [Queue Pipeline](#queue-pipeline)
8. [Video Generation](#video-generation)
9. [Template System](#template-system)
10. [Testing](#testing)
11. [Configuration](#configuration)
12. [Common Patterns](#common-patterns)
13. [Constraints & Rules](#constraints--rules)
14. [Development Workflow](#development-workflow)

---

## 🏗️ Architecture Overview

### Design Philosophy

- **Local-first:** All processing happens locally on the user's machine
- **Zero-cost:** No paid APIs, no cloud services, no Docker, no GPU requirements
- **Sequential processing:** One job at a time to prevent resource overload
- **Persistent state:** SQLite database with job tracking and recovery
- **Fail-safe:** Idempotent operations, retry logic, duplicate upload protection

### System Flow

```
User Input → Queue Job → Research → Script → TTS → Video → Thumbnail → YouTube Upload → Schedule
              ↓           ↓         ↓      ↓      ↓         ↓          ↓           ↓
            Database   AI Groq  Project  Audio  Visuals  FFmpeg    OAuth    YouTube API
```

---

## 🛠️ Technology Stack

### Backend
- **Language:** Python 3.13.6
- **Framework:** FastAPI
- **Database:** SQLite (SQLAlchemy ORM)
- **Validation:** Pydantic
- **Testing:** pytest
- **Audio:** Edge-TTS (primary), pyttsx3 (fallback)
- **Video:** FFmpeg (local binary)
- **Captions:** Whisper tiny (local model)
- **AI/LLM:** Groq API (configurable, local placeholder available)

### Frontend
- **Framework:** React + TypeScript
- **Build:** Vite
- **State:** React hooks
- **API:** Fetch with custom service layer

### External Services
- **YouTube API:** OAuth 2.0 authentication
- **Groq API:** Optional AI content generation
- **No paid infrastructure:** All processing local

---

## 📁 Directory Structure

```
youtube-uploader/
├── backend/
│   ├── main.py                          # FastAPI app entry point
│   ├── db.py                            # SQLAlchemy Base and engine
│   ├── auth.py                          # OAuth authentication
│   ├── youtube.py                       # YouTube API functions
│   ├── queue_models.py                  # Queue ORM + Pydantic models
│   ├── video_generation_models.py       # Video job models
│   ├── content_models.py                # Content project models
│   ├── tts_models.py                    # TTS audio models
│   ├── routers/                         # API route modules
│   │   ├── queue.py                     # Queue endpoints
│   │   ├── video_generation.py         # Video generation endpoints
│   │   ├── youtube_playlists.py         # Playlist endpoints (Phase 3D)
│   │   ├── templates.py                 # Template endpoints (Phase 3E.1)
│   │   ├── content.py                   # Content generation endpoints
│   │   ├── tts.py                       # TTS endpoints
│   │   ├── auth.py                      # Auth endpoints
│   │   └── uploads.py                   # Legacy upload endpoints
│   └── services/
│       ├── queue_processor.py           # Queue worker logic
│       ├── video/
│       │   ├── pipeline.py              # Video generation pipeline
│       │   ├── visual_builder.py        # Image/card generation
│       │   ├── templates.py            # Template registry (Phase 3E.1)
│       │   ├── media_utils.py          # FFmpeg utilities
│       │   └── caption.py               # Caption generation
│       ├── content/
│       │   ├── research.py             # Web research
│       │   ├── script_generation.py    # AI script generation
│       │   └── llm_providers.py        # LLM provider factory
│       └── tts/
│           ├── edge_tts.py             # Edge-TTS provider
│           └── local_tts.py            # Local TTS fallback
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   └── QueuePage.tsx           # Main queue UI
│   │   ├── services/
│   │   │   └── api.ts                  # API client functions
│   │   └── types/
│   │       └── api.ts                  # TypeScript type definitions
│   └── package.json
├── tests/
│   ├── conftest.py                     # Test fixtures
│   ├── test_api.py                     # API tests
│   ├── test_queue.py                   # Queue tests
│   ├── test_content_api.py             # Content tests
│   ├── test_tts_api.py                 # TTS tests
│   ├── test_video_generation.py       # Video tests
│   ├── test_ffmpeg.py                  # FFmpeg tests
│   └── .gitignore                      # Tests excluded from git
├── data/                               # Generated data (gitignored)
│   ├── audio/                          # TTS output
│   ├── videos/                         # Generated videos
│   ├── thumbnails/                     # Video thumbnails
│   ├── captions/                       # SRT caption files
│   ├── temp/                           # Per-job working files
│   └── models/                         # Downloaded AI models
├── upload_tmp/                         # Upload working directory (gitignored)
├── uploads.db                          # SQLite database (gitignored)
├── credentials.json                    # OAuth credentials (gitignored)
├── token.json                          # OAuth token (gitignored)
├── auth.py                             # Top-level auth utilities
├── youtube.py                          # Top-level YouTube utilities
├── .gitignore                          # Git ignore rules
└── CODEBASE_GUIDE.md                   # This file
```

---

## 🔧 Core Systems

### 1. Queue System (`backend/services/queue_processor.py`)

**Purpose:** Orchestrates the entire content generation pipeline.

**Key Functions:**
- `claim_next_job()` - Claims next job with mutex protection
- `_run_full_pipeline()` - Runs all stages for a job
- `_stage_research_and_script()` - Research + script generation
- `_stage_tts()` - Text-to-speech audio generation
- `_stage_video()` - Video generation
- `_stage_youtube()` - YouTube upload and scheduling

**Status Lifecycle:**
```
queued → researching → generating_script → generating_audio → generating_video → uploading → scheduled/completed
        ↓           ↓                  ↓                 ↓                ↓           ↓
      failed      failed              failed            failed           failed      failed
```

**Special Statuses:**
- `awaiting_auth` - OAuth token invalid, needs reauthorization
- `paused` - Queue worker paused
- `cancelled` - User cancelled the job

**Key Features:**
- Sequential execution (one job at a time)
- Startup recovery for stale jobs
- Upload-only retry (skip generation if video exists)
- Duplicate upload protection
- Timestamp-based retry delays

### 2. Video Generation (`backend/services/video/pipeline.py`)

**Purpose:** Generates video from audio and script.

**Pipeline Stages:**
1. Load audio and script
2. Calculate scene durations proportional to narration length
3. Build scene images (title card + scene cards)
4. Generate fallback captions
5. Concatenate scene clips
6. Mix audio
7. Burn captions into video
8. Generate thumbnail
9. Probe final video

**Key Functions:**
- `run_pipeline()` - Async entry point
- `_run_pipeline_sync()` - Synchronous pipeline in thread
- `calculate_scene_durations()` - Proportional timing based on character count

**Template Integration (Phase 3E.1):**
- Accepts `template_id` parameter
- Passes template to visual builder
- Routes to template-specific rendering functions

### 3. Visual Builder (`backend/services/video/visual_builder.py`)

**Purpose:** Generates PNG cards for video scenes.

**Key Functions:**
- `generate_title_card()` - Opening title card
- `generate_scene_card()` - Scene narration cards
- `_generate_minimal_dark_title_card()` - Minimal dark template
- `_generate_quote_fact_title_card()` - Quote/fact template
- `_generate_minimal_dark_scene_card()` - Minimal dark template
- `_generate_quote_fact_scene_card()` - Quote/fact template

**Styling:**
- Gradient backgrounds with palette selection
- Windows system fonts
- Palette based on topic seed (hash-based color selection)
- Template-specific typography and layout

### 4. Template System (`backend/services/video/templates.py`)

**Purpose:** Central registry for video templates.

**Available Templates:**
- `minimal_dark` - Original gradient style, centered text
- `quote_fact` - Bold emphasis, large quotes, centered layout

**Template Config:**
```python
@dataclass
class TemplateConfig:
    template_id: str
    name: str
    description: str
    background_type: str  # "gradient", "solid"
    layout_type: str     # "centered", "quote_emphasis"
    typography_style: str # "minimal", "bold", "classic"
    caption_style: str   # "bottom", "centered"
    supports_animation: bool
```

**Functions:**
- `get_template(template_id)` - Get template with fallback to minimal_dark
- `list_templates()` - List all templates
- `is_valid_template(template_id)` - Validate template ID
- `get_default_template()` - Return minimal_dark

### 5. TTS System (`backend/services/tts/`)

**Purpose:** Generate audio from text.

**Providers:**
- **Edge-TTS (primary):** Microsoft Edge online TTS
- **pyttsx3 (fallback):** Local Windows TTS

**Fallback Logic:**
1. Try Edge-TTS with 3 attempts
2. On failure, switch to local pyttsx3
3. If both fail, mark job as failed

**Key Functions:**
- `AutoTTSManager.synthesize()` - Auto-select provider
- `edge_tts.synthesize()` - Edge-TTS implementation
- `local_tts.synthesize()` - Local TTS implementation

### 6. YouTube Integration (`youtube.py`)

**Purpose:** YouTube API operations.

**OAuth Scopes:**
- `https://www.googleapis.com/auth/youtube.upload` - Upload videos
- `https://www.googleapis.com/auth/youtube` - Playlist management

**Key Functions:**
- `upload_video()` - Upload video to YouTube
- `list_playlists()` - List user playlists
- `add_to_playlist()` - Add video to playlist

**Phase 3D Features:**
- Made-for-kids designation (`status.selfDeclaredMadeForKids`)
- Custom metadata (title, description, tags)
- Playlist addition after upload
- Scheduled publishing (`publishAt` with `privacyStatus=private`)

---

## 🗄️ Database Schema

### Tables

#### `content_queue_jobs`
Main queue job table.

**Key Fields:**
- `id` (PK) - UUID
- `topic` - User-provided topic
- `status` - Current status
- `template_id` - Selected template (Phase 3E.1, default: "minimal_dark")
- `youtube_video_id` - YouTube video ID after upload
- `youtube_url` - Full YouTube URL
- `video_uploaded` - Boolean, upload completed
- `scheduled_publish_at` - UTC timestamp for scheduled publishing
- `youtube_privacy_status` - private/public/unlisted
- `made_for_kids` - COPPA compliance (Phase 3D)
- `youtube_playlist_id` - Target playlist (Phase 3D)
- `playlist_added` - Playlist addition success (Phase 3D)
- `playlist_error` - Playlist error message (Phase 3D)
- `custom_title` - Override AI title (Phase 3D)
- `custom_description` - Override AI description (Phase 3D)
- `custom_tags` - Override AI tags (Phase 3D, JSON text)
- `retry_count` - Number of retry attempts
- `next_retry_at` - UTC timestamp for next retry
- Timestamps: `created_at`, `updated_at`, `started_at`, `completed_at`, `failed_at`, `cancelled_at`

#### `video_generation_jobs`
Video generation job table.

**Key Fields:**
- `id` (PK) - UUID
- `content_project_id` - FK to content project
- `audio_id` - FK to audio record
- `status` - Current status
- `template_id` - Template used (Phase 3E.1, default: "minimal_dark")
- `width`, `height`, `fps` - Video parameters
- `captions_enabled`, `music_enabled` - Feature flags
- `file_path` - Output video file path
- `thumbnail_path` - Thumbnail file path
- `caption_path` - SRT caption file path
- `duration_seconds` - Video duration
- `file_size_bytes` - File size
- Timestamps: `created_at`, `updated_at`, `completed_at`

#### `content_projects`
Content project table (research + script).

**Key Fields:**
- `id` (PK) - UUID
- `topic` - Research topic
- `language`, `tone` - Content parameters
- `target_duration_seconds`, `scene_count` - Generation parameters
- `status` - Current status
- `research_data` - JSON research results
- `script_data` - JSON script with scenes
- Timestamps: `created_at`, `updated_at`

#### `generated_audio`
TTS audio records.

**Key Fields:**
- `id` (PK) - UUID
- `content_project_id` - FK to content project
- `voice` - Voice identifier
- `language` - Audio language
- `file_path` - Audio file path
- `file_size_bytes` - File size
- `duration_seconds` - Audio duration
- `provider` - "edge" or "local"
- `status` - Current status
- Timestamps: `created_at`, `updated_at`, `completed_at`

#### `youtube_playlists`
Playlist cache table (Phase 3D).

**Key Fields:**
- `id` (PK) - Playlist ID from YouTube
- `title` - Playlist title
- `description` - Playlist description
- `item_count` - Number of videos
- `cached_at` - Cache timestamp

---

## 🌐 API Endpoints

### Queue Endpoints (`/api/queue`)

- `POST /api/queue` - Create queue jobs (bulk)
- `GET /api/queue` - List all queue jobs
- `GET /api/queue/{job_id}` - Get specific job
- `DELETE /api/queue/{job_id}` - Delete job
- `POST /api/queue/{job_id}/cancel` - Cancel job
- `POST /api/queue/{job_id}/retry` - Retry failed job
- `PATCH /api/queue/{job_id}/metadata` - Update metadata (Phase 3D)

### Queue Control Endpoints

- `POST /api/queue/start` - Start queue worker
- `POST /api/queue/stop` - Stop queue worker
- `POST /api/queue/pause` - Pause queue worker
- `POST /api/queue/resume` - Resume queue worker
- `POST /api/queue/retry-all-failed` - Retry all failed jobs
- `POST /api/queue/clear-completed` - Clear completed jobs
- `POST /api/queue/clear-failed` - Clear failed jobs
- `POST /api/queue/cleanup` - Cleanup old files

### Queue Info Endpoints

- `GET /api/queue/health` - Queue health status
- `GET /api/queue/stats` - Queue statistics
- `GET /api/queue/status` - Queue status summary
- `GET /api/queue/{job_id}/logs` - Job logs

### Template Endpoints (`/api/templates`) - Phase 3E.1

- `GET /api/templates` - List all templates
- `GET /api/templates/{template_id}` - Get template details

### Playlist Endpoints (`/api/youtube/playlists`) - Phase 3D

- `GET /api/youtube/playlists` - List cached playlists
- `POST /api/youtube/playlists/refresh` - Force refresh playlist cache

### Video Generation Endpoints (`/api/video-generation`)

- `POST /api/video-generation/from-content/{project_id}` - Create video job
- `GET /api/video-generation` - List video jobs
- `GET /api/video-generation/{job_id}` - Get video job
- `DELETE /api/video-generation/{job_id}` - Delete video job
- `GET /api/video-generation/{job_id}/video` - Stream video
- `GET /api/video-generation/{job_id}/thumbnail` - Stream thumbnail
- `GET /api/video-generation/{job_id}/captions` - Download captions

### Content Endpoints (`/api/content`)

- `POST /api/content/research` - Research topic
- `POST /api/content/generate-script` - Generate script from research
- `GET /api/content/{project_id}` - Get content project
- `GET /api/content` - List content projects

### TTS Endpoints (`/api/tts`)

- `POST /api/tts/generate-from-content/{project_id}` - Generate audio
- `GET /api/tts/{audio_id}` - Get audio record
- `GET /api/tts/{audio_id}/audio` - Stream audio
- `DELETE /api/tts/{audio_id}` - Delete audio
- `GET /api/tts/voices` - List available voices

### Auth Endpoints (`/api/auth`)

- `GET /api/auth/status` - Auth status
- `POST /api/auth/authorize` - Start OAuth flow
- `GET /api/auth/callback` - OAuth callback

---

## 🔄 Queue Pipeline

### Job Lifecycle

1. **Creation:**
   - User submits topic(s) via bulk form
   - `POST /api/queue` creates `ContentQueueJob` records
   - Jobs start in `queued` status

2. **Research & Script:**
   - Worker claims job
   - Calls web research API (Groq or placeholder)
   - Generates script with scenes
   - Updates status to `generating_script` → `completed`

3. **TTS Audio:**
   - Calls TTS system (Edge-TTS → local fallback)
   - Creates `GeneratedAudio` record
   - Updates status to `generating_audio` → `completed`

4. **Video Generation:**
   - Creates `VideoGenerationJob` record
   - Calls `run_pipeline()` with template_id
   - Generates visual cards using template
   - FFmpeg concatenates and encodes
   - Updates status to `generating_video` → `completed`

5. **YouTube Upload:**
   - Uploads video to YouTube
   - Applies metadata (custom or AI-generated)
   - Adds to playlist if specified
   - Sets scheduled publish time if specified
   - Updates status to `uploading` → `completed` or `scheduled`

6. **Error Handling:**
   - Transient errors → retry with delay
   - Permanent errors → mark as failed
   - OAuth errors → set to `awaiting_auth`
   - Max retries → mark as failed

### Startup Recovery

Worker checks for stale jobs on startup:
- Jobs in active statuses for > 1 hour → requeue
- Jobs with `youtube_video_id` → mark as completed
- Jobs with exhausted retries → mark as failed

---

## 🎬 Video Generation

### Process Flow

```
1. Load script and audio
2. Calculate scene durations (proportional to narration length)
3. Generate title card (template-specific)
4. Generate scene cards (template-specific)
5. Generate fallback captions (SRT format)
6. Concatenate scene clips with FFmpeg
7. Mix audio with FFmpeg
8. Burn captions with FFmpeg
9. Generate thumbnail (first frame)
10. Probe final video for metadata
```

### Template Integration (Phase 3E.1)

**Pipeline receives:**
- `template_id` from queue job (default: "minimal_dark")

**Pipeline does:**
- Calls `get_template(template_id)` to get config
- Passes template to `generate_title_card()` and `generate_scene_card()`
- Visual builder routes to template-specific functions

**Visual builder routing:**
```python
if template.template_id == "quote_fact":
    return _generate_quote_fact_scene_card(...)
else:  # minimal_dark and future templates
    return _generate_minimal_dark_scene_card(...)
```

### Template-Specific Rendering

**minimal_dark:**
- 12px accent bar (left edge)
- Scene number pill (top-left)
- Horizontal divider
- Script title (top)
- Narration excerpt (center)
- Visual hint (bottom)
- Standard fonts: 72px title, 40px body, 28px small

**quote_fact:**
- 24px accent bar (left edge, thicker)
- Large quote mark at top
- Centered quote text (larger fonts)
- Subtle scene number (bottom-right)
- Bold fonts: 96px title, 52px body, 32px small
- Accent-colored quote text

---

## 🎨 Template System (Phase 3E.1)

### Template Registry

Located in `backend/services/video/templates.py`.

**Current Templates:**
1. **minimal_dark** - Original gradient style
2. **quote_fact** - Bold quote emphasis

### Adding New Templates

1. Add to `TEMPLATE_REGISTRY` in `templates.py`:
```python
"new_template": TemplateConfig(
    template_id="new_template",
    name="New Template",
    description="Description",
    background_type="gradient",
    layout_type="centered",
    typography_style="minimal",
    caption_style="bottom",
    supports_animation=False,
)
```

2. Add rendering functions in `visual_builder.py`:
```python
def _generate_new_template_title_card(...):
    # Implementation

def _generate_new_template_scene_card(...):
    # Implementation
```

3. Add routing in `visual_builder.py`:
```python
elif template.template_id == "new_template":
    return _generate_new_template_scene_card(...)
```

4. Update frontend to include in template selector

### Template Fallback

Invalid template IDs automatically fall back to `minimal_dark`:
```python
def get_template(template_id: str) -> TemplateConfig:
    template = TEMPLATE_REGISTRY.get(template_id)
    if template is None:
        return TEMPLATE_REGISTRY["minimal_dark"]
    return template
```

---

## 🧪 Testing

### Test Structure

- **Unit tests:** Individual function tests
- **Integration tests:** API endpoint tests
- **Regression tests:** Phase-specific test suites

### Test Commands

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_queue.py -v

# Run specific test
python -m pytest tests/test_queue.py::TestCreateQueueJobs::test_bulk_create_valid -v

# Run with coverage
python -m pytest tests/ --cov=backend --cov-report=html
```

### Test Files

- `test_api.py` - Basic API tests
- `test_queue.py` - Queue core tests (45 tests)
- `test_queue_3b.py` - Phase 3B reliability tests (80+ tests)
- `test_queue_3c.py` - Phase 3C reliability tests (20 tests)
- `test_queue_3d.py` - Phase 3D metadata tests (18 tests)
- `test_templates_3e1.py` - Phase 3E.1 template tests (22 tests)
- `test_content_api.py` - Content generation tests
- `test_tts_api.py` - TTS tests
- `test_video_generation.py` - Video generation tests
- `test_ffmpeg.py` - FFmpeg utility tests

### Test Fixtures

Located in `tests/conftest.py`:
- `client` - FastAPI TestClient
- `db` - SQLAlchemy test session
- `reset_db` - Database reset between tests

### Current Test Status

**Total:** 504 tests passing ✅

---

## ⚙️ Configuration

### Environment Variables

Set in `.env` file (gitignored):

```bash
# Groq API (optional, for AI content generation)
GROQ_API_KEY=your_key_here

# FFmpeg paths (optional, defaults to system PATH)
FFMPEG_PATH=G:/tools/ffmpeg/bin/ffmpeg.exe
FFPROBE_PATH=G:/tools/ffmpeg/bin/ffprobe.exe

# Queue worker
QUEUE_AUTO_RUN=1  # Auto-start worker on server start

# Retry delays (seconds)
QUEUE_RETRY_DELAY_MIN=30
QUEUE_RETRY_DELAY_MAX=3600

# Upload limits
QUEUE_DAILY_UPLOAD_LIMIT=50

# Disk space thresholds (GB)
QUEUE_DISK_WARNING_GB=10
QUEUE_DISK_CRITICAL_GB=5
```

### Frontend Configuration

Set in `frontend/.env` (gitignored):

```bash
VITE_API_URL=http://localhost:8000
```

### Database

- **File:** `uploads.db` (SQLite)
- **Location:** Project root
- **Schema:** Created via `Base.metadata.create_all()`
- **Migrations:** Manual (no Alembic)
- **Backups:** Created before schema changes (`.bak_*` files)

---

## 📐 Common Patterns

### 1. Database Session Pattern

```python
from backend.db import SessionLocal

db = SessionLocal()
try:
    # Database operations
    db.commit()
except Exception as e:
    db.rollback()
    raise
finally:
    db.close()
```

### 2. API Response Pattern

```python
from pydantic import BaseModel

class ResponseModel(BaseModel):
    field: str

@router.get("/endpoint")
def endpoint() -> ResponseModel:
    # Logic
    return ResponseModel(field="value")
```

### 3. Background Task Pattern

```python
from fastapi import BackgroundTasks

@router.post("/start")
def start_task(background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_long_task, arg1, arg2)
    return {"status": "started"}
```

### 4. Error Handling Pattern

```python
from fastapi import HTTPException

try:
    # Operation
except SpecificError as e:
    logger.error("Operation failed: %s", e)
    raise HTTPException(status_code=500, detail=str(e))
```

### 5. Template Fallback Pattern

```python
from backend.services.video.templates import get_template

template = get_template(template_id)  # Falls back to minimal_dark
```

### 6. Queue Job Status Pattern

```python
from backend.queue_models import QueueStatus

job.status = QueueStatus.QUEUED
db.commit()
```

---

## 🚫 Constraints & Rules

### Architecture Constraints

1. **No paid APIs** - All processing must be local or free
2. **No Docker** - No containerization
3. **No GPU** - CPU-only processing
4. **No parallel video generation** - One job at a time
5. **No automatic asset downloads** - User provides assets manually
6. **No breaking changes** - Preserve backward compatibility

### Database Constraints

1. **No Alembic** - Manual schema changes only
2. **SQLite only** - No PostgreSQL/MySQL
3. **Single database file** - `uploads.db`
4. **Backup before changes** - Always backup before schema changes

### Code Style Constraints

1. **No comments in code** - Unless asked by user
2. **Compact code** - Avoid unnecessary nesting
3. **Idiomatic Python** - Follow PEP 8
4. **Type hints** - Use Python type hints
5. **Pydantic validation** - Use Pydantic for API validation

### Security Constraints

1. **No secrets in code** - Use environment variables
2. **No credentials in responses** - Never expose API keys/tokens
3. **No file paths in responses** - Use URLs instead
4. **OAuth scope validation** - Only request necessary scopes

### Testing Constraints

1. **Mock external services** - No real API calls in tests
2. **Test database** - Use separate test database
3. **Cleanup after tests** - Reset database between tests
4. **Test coverage** - Maintain high test coverage

---

## 🔄 Development Workflow

### Adding New Features

1. **Plan:**
   - Review architecture constraints
   - Check for breaking changes
   - Design backward-compatible changes

2. **Implement:**
   - Add database fields with defaults
   - Update models (ORM + Pydantic)
   - Add/modify API endpoints
   - Update services
   - Update frontend if needed

3. **Test:**
   - Write unit tests
   - Write integration tests
   - Run full test suite
   - Fix failures

4. **Verify:**
   - Manual testing if needed
   - Check backward compatibility
   - Verify no regressions

5. **Commit:**
   - Backup database if schema changed
   - Stage changes
   - Commit with descriptive message
   - Push to GitHub

### Database Schema Changes

1. **Backup current database:**
   ```bash
   cp uploads.db uploads.db.bak_YYYYMMDD_HHMMSS
   ```

2. **Update models:**
   - Add fields with defaults
   - Update ORM models
   - Update Pydantic models

3. **Recreate database:**
   ```python
   from backend.db import Base, engine
   Base.metadata.drop_all(bind=engine)
   Base.metadata.create_all(bind=engine)
   ```

4. **Test:**
   - Run test suite
   - Verify new fields work
   - Check backward compatibility

### Debugging Tips

1. **Check logs:**
   - Backend logs in console
   - Queue processor logs detailed progress

2. **Database inspection:**
   ```bash
   sqlite3 uploads.db
   .tables
   .schema content_queue_jobs
   ```

3. **API testing:**
   ```bash
   curl http://localhost:8000/api/queue
   ```

4. **Test specific function:**
   ```bash
   python -m pytest tests/test_queue.py::test_name -v -s
   ```

---

## 📝 Notes for AI Agents

### When Working on This Codebase

1. **Always check constraints** - Review architecture constraints before making changes
2. **Preserve backward compatibility** - Add defaults for new fields
3. **Test thoroughly** - Run full test suite before committing
4. **Backup database** - Before any schema changes
5. **Mock external services** - No real API calls in tests
6. **Keep it local** - No cloud services, no paid APIs
7. **Follow patterns** - Use existing code patterns

### Common Pitfalls

1. **Database schema mismatch** - Models updated but database not recreated
2. **Import issues** - Lazy imports causing mock failures
3. **Missing defaults** - New fields without defaults break existing jobs
4. **External API calls in tests** - Should be mocked
5. **Secrets in code** - Should use environment variables
6. **Breaking changes** - Should preserve backward compatibility

### Quick Reference

- **Start server:** `uvicorn backend.main:app --reload`
- **Run tests:** `python -m pytest tests/ -v`
- **Frontend build:** `cd frontend && npm run build`
- **Database backup:** `cp uploads.db uploads.db.bak_$(date +%Y%m%d_%H%M%S)`
- **Recreate database:** Python script with `Base.metadata.create_all()`

---

## 📞 Support

For issues or questions:
- Check this guide first
- Review test files for examples
- Check existing code patterns
- Maintain backward compatibility

---

**End of Codebase Guide**
