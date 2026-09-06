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

// ── Phase 2B: TTS Audio Generation ──────────────────────────────────────────

export type AudioStatus = 'pending' | 'generating' | 'completed' | 'failed';

export interface TTSVoice {
  name: string;
  locale: string;
  language: string;
  gender: string;
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

export interface TTSVoice {
  name: string;
  locale: string;
  language: string;
  gender: string;
  provider: string;   // "edge" | "local"
}
