// All types matching the FastAPI backend models

export type JobStatus =
  | 'pending'
  | 'uploading'
  | 'scheduled'
  | 'published'
  | 'failed'
  | 'cancelled';

export type PrivacyStatus = 'private' | 'unlisted' | 'public';

export interface UploadJob {
  job_id: string;
  video_id: string | null;
  filename: string;
  title: string;
  description: string;
  tags: string[];
  category_id: string;
  privacy_status: PrivacyStatus;
  status: JobStatus;
  progress: number;
  scheduled_at: string | null;
  created_at: string;
  updated_at: string;
  error_message: string | null;
  url: string | null;
}

export interface AuthStatus {
  authenticated: boolean;
  message: string;
}

export interface HealthResponse {
  status: string;
  version: string;
}

export interface VideoUpdateRequest {
  title?: string;
  description?: string;
  tags?: string[];
  privacy_status?: PrivacyStatus;
  scheduled_at?: string | null;
  clear_schedule?: boolean;
  timezone?: string;
}

export interface VideoUpdateResponse {
  video_id: string;
  message: string;
}

export interface ApiError {
  detail: string;
}

// ── Phase 2A: AI Content Generation ─────────────────────────────────────────

export type ContentStatus =
  | 'pending'
  | 'researching'
  | 'generating'
  | 'completed'
  | 'failed';

export interface ResearchSource {
  title: string;
  url: string;
  snippet: string;
  key_points: string[];
}

export interface ResearchResponse {
  topic: string;
  sources: ResearchSource[];
  key_facts: string[];
  research_summary: string;
}

export interface Scene {
  scene_number: number;
  narration: string;
  visual_description: string;
  estimated_duration_seconds: number;
}

export interface GeneratedScript {
  title: string;
  description: string;
  tags: string[];
  hook: string;
  estimated_duration_seconds: number;
  scenes: Scene[];
}

export interface ContentProjectSummary {
  id: string;
  topic: string;
  language: string;
  tone: string;
  target_duration_seconds: number;
  scene_count: number;
  status: ContentStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  has_script: boolean;
}

export interface ContentProjectDetail extends ContentProjectSummary {
  sources: ResearchSource[];
  script: GeneratedScript | null;
}

export interface ScriptRequest {
  topic: string;
  language: string;
  tone: string;
  target_duration_seconds: number;
  scene_count: number;
}

export interface ResearchRequest {
  topic: string;
  language: string;
  depth: 'quick' | 'standard' | 'deep';
}

// ── Phase 2B/2C: TTS Audio Generation ───────────────────────────────────────

export type AudioStatus = 'pending' | 'generating' | 'completed' | 'failed';

export interface TTSVoice {
  name: string;
  locale: string;
  language: string;
  gender: string;
  provider: string;   // "edge" | "local"
}

export interface AudioRecord {
  id: string;
  content_project_id: string;
  voice: string;
  language: string;
  text_length: number;
  file_size_bytes: number | null;
  duration_seconds: number | null;
  status: AudioStatus;
  error_message: string | null;
  provider: string | null;   // "edge" | "local" | null
  created_at: string;
  updated_at: string;
  audio_url: string | null;
}

export interface TTSGenerateFromContentRequest {
  voice?: string;
}

// ── Phase 2D: Video Generation ───────────────────────────────────────────────

export type VideoJobStatus =
  | 'queued'
  | 'preparing'
  | 'generating_audio'
  | 'preparing_visuals'
  | 'generating_captions'
  | 'assembling'
  | 'generating_thumbnail'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface VideoGenerationRequest {
  audio_id?: string;
  width?: number;
  height?: number;
  fps?: number;
  captions_enabled?: boolean;
  music_enabled?: boolean;
  // Phase 3E.1: Template support
  template_id?: string;
  // Phase 3E.2: Aspect ratio support
  aspect_ratio?: string;
}

export interface VideoJob {
  id: string;
  content_project_id: string;
  audio_id: string | null;
  status: VideoJobStatus;
  progress: number;
  current_step: string | null;
  duration_seconds: number | null;
  width: number;
  height: number;
  fps: number;
  file_size_bytes: number | null;
  captions_enabled: boolean;
  music_enabled: boolean;
  // Phase 3E.1: Template support
  template_id: string;
  // Phase 3E.2: Aspect ratio support
  aspect_ratio: string;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  video_url: string | null;
  thumbnail_url: string | null;
  caption_url: string | null;
}

// ── Phase 3E.1: Video Templates ───────────────────────────────────────────────

export interface VideoTemplate {
  template_id: string;
  name: string;
  description: string;
  background_type: string;
  layout_type: string;
  typography_style: string;
  caption_style: string;
  supports_animation: boolean;
}

// ── Phase 3A: Content Queue ──────────────────────────────────────────────────

export type QueueJobStatus =
  | 'queued'
  | 'researching'
  | 'generating_script'
  | 'generating_audio'
  | 'generating_video'
  | 'uploading'
  | 'scheduled'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'paused'
  | 'awaiting_auth';

export interface QueueJob {
  id: string;
  topic: string;
  language: string;
  tone: string;
  target_duration_seconds: number;
  scene_count: number;
  status: QueueJobStatus;
  current_stage: string | null;
  progress: number;
  priority: number;
  retry_count: number;
  max_retries: number;
  scheduled_publish_at: string | null;
  youtube_privacy_status: string;
  youtube_category_id: string;
  content_project_id: string | null;
  video_job_id: string | null;
  youtube_video_id: string | null;
  youtube_url: string | null;
  video_uploaded: boolean;
  thumbnail_uploaded: boolean;
  schedule_set: boolean;
  error_message: string | null;
  last_error_type: string | null;
  disk_usage_bytes: number | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  failed_at: string | null;
  cancelled_at: string | null;
  next_retry_at: string | null;
  // Phase 3D fields
  made_for_kids: boolean;
  youtube_playlist_id: string | null;
  playlist_added: boolean;
  playlist_error: string | null;
  custom_title: string | null;
  custom_description: string | null;
  custom_tags: string[] | null;
  youtube_studio_url: string | null;
  // Phase 3E.1: Template support
  template_id: string;
  // Phase 3E.2: Aspect ratio support
  aspect_ratio: string;
  // Phase 3E.3: Background visual support
  background_type: string | null;
  background_path: string | null;
  background_color: string | null;
  background_fit: string | null;
  // Phase 3G: Production observability
  production_stage: string | null;
  summary: string | null;
  // Phase 3I: Audio profiles
  tts_voice: string | null;
  music_style: string | null;
  // Phase 3H: Manifest availability flag
  manifest_available: boolean;
}

// ── Phase 3H: Render Manifest ────────────────────────────────────────────────

export interface RenderManifestScene {
  scene_index: number;
  duration_seconds: number | null;
  background_type: string | null;
  background_path: string | null;
  background_loop: boolean;
  clip_path: string | null;
  fallback_used: boolean;
  fallback_reason: string | null;
}

export interface RenderManifest {
  job_id: string;
  timestamp: string;
  production_stage: string;
  aspect_ratio: string;
  width: number;
  height: number;
  scene_count: number;
  narration_path: string | null;
  narration_duration_seconds: number | null;
  scenes: RenderManifestScene[];
  clip_paths: string[];
  final_mp4_path: string | null;
  final_duration_seconds: number | null;
  final_file_size_bytes: number | null;
  final_sha256: string | null;
  elapsed_seconds: number | null;
  fallback_count: number;
  fallback_actions: string[];
  errors: string[];
}

// ── Phase 3E.2: Aspect Ratio ───────────────────────────────────────────────────

export interface AspectRatio {
  aspect_ratio: string;
  width: number;
  height: number;
  label: string;
  description: string;
}

// ── Phase 3D: YouTube Playlist ───────────────────────────────────────────────

export interface YouTubePlaylist {
  id: string;
  title: string;
  description: string;
  item_count: number;
  cached_at: string;
}

export interface BulkQueueRequest {
  topics: string[];
  language?: string;
  tone?: string;
  target_duration_seconds?: number;
  scene_count?: number;
  priority?: number;
  max_retries?: number;
  schedule_start?: string;
  schedule_interval_minutes?: number;
  schedule_timezone?: string;
  youtube_privacy_status?: string;
  youtube_category_id?: string;
  // Phase 3D fields
  made_for_kids?: boolean;
  youtube_playlist_id?: string;
  custom_title?: string;
  custom_description?: string;
  custom_tags?: string[];
  // Phase 3E.1: Template support
  template_id?: string;
  // Phase 3E.2: Aspect ratio support
  aspect_ratio?: string;
  // Phase 3E.3: Background visual support
  background_type?: string;
  background_path?: string;
  background_color?: string;
  background_fit?: string;
  // Phase 3I: Audio profiles
  tts_voice?: string;
  music_style?: string;
}

export interface BulkQueueResponse {
  created: number;
  jobs: QueueJob[];
  schedule_summary: string[];
}

export interface QueueStatusSummary {
  queue_running: boolean;
  queue_paused: boolean;
  current_job_id: string | null;
  total: number;
  queued: number;
  processing: number;
  completed: number;
  failed: number;
  cancelled: number;
  paused: number;
}

// ── Phase 3B: Queue additional types ─────────────────────────────────────────

export interface CleanupResult {
  files_deleted: number;
  bytes_freed: number;
  files_skipped: number;
  errors: string[];
  dry_run?: boolean;
}

export interface QueueHealth {
  status: 'ok' | 'warning' | 'error';
  worker_alive: boolean;
  worker_paused: boolean;
  free_disk_gb: number;
  disk_warning: boolean;
  uploads_today: number;
  upload_limit: number;
  uploads_remaining: number;
  queued_jobs: number;
  active_jobs: number;
  details: string[];
}

// QueueStatusSummary includes current_job_id for the dashboard
export interface QueueStatusSummaryWithJobId extends QueueStatusSummary {
  current_job_id: string | null;
}

export interface QueueStats {
  total: number;
  queued: number;
  processing: number;
  completed: number;
  failed: number;
  cancelled: number;
  scheduled: number;
  uploads_today: number;
  upload_limit: number;
  free_disk_gb: number;
  avg_processing_minutes: number;
}

export interface JobLogEntry {
  id: string;
  level: string;
  stage: string | null;
  message: string;
  created_at: string;
}
