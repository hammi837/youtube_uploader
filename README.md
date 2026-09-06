# YouTube Uploader

A CLI tool and FastAPI backend for uploading and scheduling YouTube videos
using the YouTube Data API v3.

---

## Project structure

```
youtube-uploader/
├── auth.py                      # Google OAuth2 flow and token management
├── youtube.py                   # YouTube API service layer (upload, update, thumbnail)
├── main.py                      # CLI entrypoint
├── create_test_video.py         # Utility: generate a test MP4 without FFmpeg
│
├── backend/
│   ├── main.py                  # FastAPI application
│   ├── db.py                    # SQLAlchemy engine + session
│   ├── models.py                # ORM models + Pydantic schemas
│   ├── routers/
│   │   ├── auth.py              # GET /api/auth/status
│   │   ├── uploads.py           # POST /api/uploads, GET status, DELETE
│   │   └── videos.py            # GET /api/videos, PATCH, thumbnail, cancel
│   └── services/
│       ├── scheduler.py         # Timezone-aware datetime parsing + validation
│       └── youtube.py           # Backend YouTube service wrapper
│
├── tests/
│   ├── test_scheduler.py        # 22 unit tests for scheduling logic
│   └── test_api.py              # 30 API tests (no real YouTube calls)
│
├── .env                         # Config (not committed)
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Requirements

- Python 3.10+
- A Google Cloud project with **YouTube Data API v3** enabled
- OAuth 2.0 Desktop App credentials (`credentials.json`)

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Google Cloud setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the **YouTube Data API v3**
3. Create **OAuth 2.0 Client ID** credentials — choose **Desktop app**
4. Download the JSON and save as `credentials.json` in the project root

### 3. First run — authorize your account

Run any upload command. Your browser will open for Google sign-in.
After authorization, `token.json` is saved and reused automatically.

---

## CLI usage

### Normal upload (immediate, private)

```bash
python main.py --file video.mp4 --title "My Video"
```

### Upload with all options

```bash
python main.py \
  --file video.mp4 \
  --title "My Video" \
  --description "A short description" \
  --tags python tutorial coding \
  --privacy public \
  --category 28
```

### Scheduled upload

```bash
python main.py --file video.mp4 --title "Scheduled Video" --schedule "2026-09-05T18:00:00"
```

The time is treated as **local machine time** by default. Use an explicit
UTC offset to be unambiguous:

```bash
# Pakistan Standard Time (UTC+5, no DST)
python main.py --file video.mp4 --title "Scheduled" --schedule "2026-09-05T18:00:00+05:00"

# UTC explicitly
python main.py --file video.mp4 --title "Scheduled" --schedule "2026-09-05T13:00:00Z"
```

### All CLI flags

| Flag | Short | Default | Description |
|---|---|---|---|
| `--file` | `-f` | required | Path to video file |
| `--title` | `-t` | required | Video title (max 100 chars) |
| `--description` | `-d` | `""` | Video description (max 5000 chars) |
| `--tags` | | `[]` | Space-separated tags |
| `--category` | | `22` | YouTube category ID |
| `--privacy` | | `private` | `private`, `unlisted`, or `public` |
| `--schedule` | `-s` | None | Scheduled publish time (ISO 8601) |

---

## FastAPI backend

### Start the server

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

Interactive docs available at: `http://localhost:8000/docs`

### Environment variables (`.env`)

| Variable | Default | Description |
|---|---|---|
| `CREDENTIALS_FILE` | `credentials.json` | OAuth client secrets file |
| `TOKEN_FILE` | `token.json` | Cached OAuth token |
| `DATABASE_URL` | `sqlite:///./uploads.db` | SQLAlchemy database URL |
| `UPLOAD_TEMP_DIR` | `./upload_tmp` | Temp dir for uploaded files |

---

## API endpoints

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Server health check |

```bash
curl http://localhost:8000/api/health
```

### Auth

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/auth/status` | Check OAuth token status |

```bash
curl http://localhost:8000/api/auth/status
```

### Uploads

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/uploads` | Start a new upload job |
| `GET` | `/api/uploads/{job_id}/status` | Poll upload progress |
| `DELETE` | `/api/uploads/{job_id}` | Delete job record (not the YouTube video) |

**Start an immediate private upload:**
```bash
curl -X POST http://localhost:8000/api/uploads \
  -F "file=@test.mp4" \
  -F "title=My Test Upload" \
  -F "privacy_status=private"
```

**Start a scheduled upload (explicit UTC offset):**
```bash
curl -X POST http://localhost:8000/api/uploads \
  -F "file=@test.mp4" \
  -F "title=Scheduled Upload" \
  -F "privacy_status=private" \
  -F "scheduled_at=2026-09-05T13:00:00+05:00"
```

**Start a scheduled upload (IANA timezone):**
```bash
curl -X POST http://localhost:8000/api/uploads \
  -F "file=@test.mp4" \
  -F "title=Scheduled Upload" \
  -F "privacy_status=private" \
  -F "scheduled_at=2026-09-05T18:00:00" \
  -F "timezone=Asia/Karachi"
```

**Poll job status:**
```bash
curl http://localhost:8000/api/uploads/{job_id}/status
```

Response fields: `job_id`, `status`, `progress` (0-100), `video_id`, `url`, `error_message`

**Job statuses:** `pending` → `uploading` → `scheduled` / `published` / `failed` / `cancelled`

**Delete a job record:**
```bash
curl -X DELETE http://localhost:8000/api/uploads/{job_id}
```

### Videos

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/videos` | List all upload jobs |
| `PATCH` | `/api/videos/{video_id}` | Update metadata / reschedule / clear schedule |
| `POST` | `/api/videos/{video_id}/thumbnail` | Upload custom thumbnail |
| `POST` | `/api/videos/{video_id}/cancel-schedule` | Cancel scheduled publish (keep private) |

**List all videos:**
```bash
curl http://localhost:8000/api/videos
```

**Update title and description:**
```bash
curl -X PATCH http://localhost:8000/api/videos/VIDEO_ID \
  -H "Content-Type: application/json" \
  -d '{"title": "New Title", "description": "Updated description"}'
```

**Reschedule a video:**
```bash
curl -X PATCH http://localhost:8000/api/videos/VIDEO_ID \
  -H "Content-Type: application/json" \
  -d '{"scheduled_at": "2026-09-10T13:00:00+05:00"}'
```

**Cancel a scheduled publish (video stays private):**
```bash
curl -X POST http://localhost:8000/api/videos/VIDEO_ID/cancel-schedule
```

Or via PATCH:
```bash
curl -X PATCH http://localhost:8000/api/videos/VIDEO_ID \
  -H "Content-Type: application/json" \
  -d '{"clear_schedule": true}'
```

**Upload a custom thumbnail:**
```bash
curl -X POST http://localhost:8000/api/videos/VIDEO_ID/thumbnail \
  -F "file=@thumbnail.jpg"
```

---

## Database schema

Table: `upload_jobs`

| Column | Type | Description |
|---|---|---|
| `id` | TEXT (UUID) | Primary key |
| `video_id` | TEXT | YouTube video ID (set after upload) |
| `filename` | TEXT | Original filename |
| `title` | TEXT | Video title |
| `description` | TEXT | Video description |
| `tags` | TEXT | Comma-separated tags |
| `category_id` | TEXT | YouTube category ID |
| `privacy_status` | TEXT | `private`, `unlisted`, `public` |
| `status` | TEXT | Job status (see below) |
| `progress` | INTEGER | Upload progress 0–100 |
| `scheduled_at` | DATETIME | UTC scheduled publish time |
| `created_at` | DATETIME | Job creation time |
| `updated_at` | DATETIME | Last update time |
| `error_message` | TEXT | Error details on failure |

---

## FFmpeg

FFmpeg is used by Phase 2D (video assembly) to combine audio narration, images,
and captions into a final MP4 file.

### Installation location

FFmpeg is installed **project-locally** — never added to the Windows PATH and
never written to `C:`:

```
G:\youtube-uploader\tools\ffmpeg\bin\ffmpeg.exe    (~139 MB)
G:\youtube-uploader\tools\ffmpeg\bin\ffprobe.exe   (~139 MB)
G:\youtube-uploader\tools\ffmpeg\bin\ffplay.exe    (~141 MB)
```

The `tools/` directory is listed in `.gitignore` — binaries are never committed.

### How the application finds FFmpeg

The backend reads `FFMPEG_PATH` (and `FFPROBE_PATH`) from `backend/.env`:

```ini
FFMPEG_PATH=G:\youtube-uploader\tools\ffmpeg\bin\ffmpeg.exe
FFPROBE_PATH=G:\youtube-uploader\tools\ffmpeg\bin\ffprobe.exe
```

The utility module `backend/services/media/ffmpeg.py` provides:

| Function | Description |
|---|---|
| `get_ffmpeg_path()` | Returns the configured path (no disk check) |
| `get_ffprobe_path()` | Returns the configured ffprobe path |
| `get_ffmpeg_version()` | Runs `ffmpeg -version`, returns first line |
| `check_ffmpeg()` | Returns `{"available": bool, "path": ..., "version": ...}` |

### Verify FFmpeg is working

```bash
# Direct binary check
G:\youtube-uploader\tools\ffmpeg\bin\ffmpeg.exe -version

# Through Python/backend
python -c "
from dotenv import load_dotenv; load_dotenv('backend/.env')
from backend.services.media.ffmpeg import check_ffmpeg
import json; print(json.dumps(check_ffmpeg(), indent=2))
"
```

Expected output:
```json
{
  "available": true,
  "path": "G:\\youtube-uploader\\tools\\ffmpeg\\bin\\ffmpeg.exe",
  "version": "ffmpeg version N-126416-g9997fd0606-20260905 ..."
}
```

### Change FFMPEG_PATH

Edit `backend/.env`:

```ini
# Point to any other ffmpeg.exe on any drive
FFMPEG_PATH=D:\my-tools\ffmpeg\bin\ffmpeg.exe
```

No code changes needed. The application picks up the new path on restart.

### Re-installing FFmpeg

If `tools/ffmpeg/bin/` is missing (e.g. after a fresh clone):

1. Download the Windows GPL build from https://github.com/BtbN/FFmpeg-Builds/releases
   - Choose: `ffmpeg-master-latest-win64-gpl.zip`
2. Extract the `bin/` folder to `G:\youtube-uploader\tools\ffmpeg\bin\`
3. Verify: `G:\youtube-uploader\tools\ffmpeg\bin\ffmpeg.exe -version`

---

## Running tests

```bash
# All tests
python -m pytest tests/ -v

# Scheduler tests only
python -m pytest tests/test_scheduler.py -v

# API tests only
python -m pytest tests/test_api.py -v
```

**52 tests total — all pass without real YouTube API calls.**

---

## How scheduling works

1. Pass `--schedule "2026-09-05T18:00:00+05:00"` (CLI) or `scheduled_at` (API)
2. The video is uploaded to YouTube as **private** (API requirement)
3. `status.publishAt` is set to the UTC RFC 3339 value
4. YouTube automatically changes the video to **public** at that exact time
5. You can reschedule or cancel via `PATCH /api/videos/{id}` as long as the video has never been publicly published

---

## YouTube API limitations

| Limitation | Detail |
|---|---|
| **Daily upload quota** | 10,000 units/day per project. One upload ≈ 1,600 units (~6 uploads/day). |
| **Scheduled → public only** | `publishAt` always transitions to `public`. Cannot schedule to `unlisted`. |
| **Reschedule restriction** | Only works if video is still private and has never been published. |
| **Minimum lead time** | ~2 minutes in the future (this tool enforces 2 min). |
| **Custom thumbnails** | Requires a verified YouTube channel (usually 100+ subscribers). |
| **Unverified project restriction** | Projects created after July 28, 2020 that haven't passed Google's audit will have all uploads forced to `private` regardless of requested privacy. |
| **Token expiry** | Access tokens expire after 1 hour; automatically refreshed via `refresh_token`. |

---

## Security

The following are never exposed via any API endpoint or log output:

- `credentials.json` — OAuth client secret
- `token.json` — access and refresh tokens
- `.env` — environment config

All three are excluded from Git via `.gitignore`.

---

## YouTube category IDs

| ID | Category |
|---|---|
| 1 | Film & Animation |
| 2 | Autos & Vehicles |
| 10 | Music |
| 17 | Sports |
| 20 | Gaming |
| 22 | People & Blogs (default) |
| 24 | Entertainment |
| 25 | News & Politics |
| 26 | How-to & Style |
| 28 | Science & Technology |
