import type {
  AudioRecord,
  AuthStatus,
  ContentProjectDetail,
  ContentProjectSummary,
  HealthResponse,
  ResearchRequest,
  ResearchResponse,
  ScriptRequest,
  TTSGenerateFromContentRequest,
  TTSVoice,
  UploadJob,
  VideoUpdateRequest,
  VideoUpdateResponse,
} from '../types/api';

// Central API base URL — configured via Vite env variable
const BASE_URL = (import.meta.env.VITE_API_URL as string) || 'http://localhost:8000';

// ── Generic helpers ─────────────────────────────────────────────────────────

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // ignore parse errors
    }
    throw new Error(detail);
  }
  // 204 No Content
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

// ── Health ───────────────────────────────────────────────────────────────────

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${BASE_URL}/api/health`);
  return handleResponse<HealthResponse>(res);
}

// ── Auth ─────────────────────────────────────────────────────────────────────

export async function getAuthStatus(): Promise<AuthStatus> {
  const res = await fetch(`${BASE_URL}/api/auth/status`);
  return handleResponse<AuthStatus>(res);
}

// ── Uploads ──────────────────────────────────────────────────────────────────

export interface UploadFormData {
  file: File;
  title: string;
  description: string;
  tags: string;           // comma-separated — backend accepts this format
  category_id: string;
  privacy_status: string;
  scheduled_at?: string;
  timezone?: string;
}

export async function createUpload(data: UploadFormData): Promise<UploadJob> {
  const form = new FormData();
  form.append('file', data.file);
  form.append('title', data.title);
  form.append('description', data.description);
  form.append('tags', data.tags);
  form.append('category_id', data.category_id);
  form.append('privacy_status', data.privacy_status);
  if (data.scheduled_at) form.append('scheduled_at', data.scheduled_at);
  if (data.timezone)     form.append('timezone', data.timezone);

  const res = await fetch(`${BASE_URL}/api/uploads`, {
    method: 'POST',
    body: form,
  });
  return handleResponse<UploadJob>(res);
}

export async function getUploadStatus(jobId: string): Promise<UploadJob> {
  const res = await fetch(`${BASE_URL}/api/uploads/${jobId}/status`);
  return handleResponse<UploadJob>(res);
}

export async function deleteUpload(jobId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/uploads/${jobId}`, {
    method: 'DELETE',
  });
  return handleResponse<void>(res);
}

// ── Videos ───────────────────────────────────────────────────────────────────

export async function getVideos(): Promise<UploadJob[]> {
  const res = await fetch(`${BASE_URL}/api/videos`);
  return handleResponse<UploadJob[]>(res);
}

export async function updateVideo(
  videoId: string,
  body: VideoUpdateRequest
): Promise<VideoUpdateResponse> {
  const res = await fetch(`${BASE_URL}/api/videos/${videoId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return handleResponse<VideoUpdateResponse>(res);
}

export async function cancelSchedule(videoId: string): Promise<VideoUpdateResponse> {
  const res = await fetch(`${BASE_URL}/api/videos/${videoId}/cancel-schedule`, {
    method: 'POST',
  });
  return handleResponse<VideoUpdateResponse>(res);
}

export async function uploadThumbnail(
  videoId: string,
  file: File
): Promise<VideoUpdateResponse> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${BASE_URL}/api/videos/${videoId}/thumbnail`, {
    method: 'POST',
    body: form,
  });
  return handleResponse<VideoUpdateResponse>(res);
}

// ── Phase 2A: AI Content Generation ─────────────────────────────────────────

export async function researchTopic(data: ResearchRequest): Promise<ResearchResponse> {
  const res = await fetch(`${BASE_URL}/api/content/research`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return handleResponse<ResearchResponse>(res);
}

export async function generateScript(data: ScriptRequest): Promise<ContentProjectDetail> {
  const res = await fetch(`${BASE_URL}/api/content/generate-script`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return handleResponse<ContentProjectDetail>(res);
}

export async function getContentProjects(): Promise<ContentProjectSummary[]> {
  const res = await fetch(`${BASE_URL}/api/content`);
  return handleResponse<ContentProjectSummary[]>(res);
}

export async function getContentProject(id: string): Promise<ContentProjectDetail> {
  const res = await fetch(`${BASE_URL}/api/content/${id}`);
  return handleResponse<ContentProjectDetail>(res);
}

export async function deleteContentProject(id: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/content/${id}`, { method: 'DELETE' });
  return handleResponse<void>(res);
}

// ── Phase 2B: TTS Audio Generation ──────────────────────────────────────────

export async function getTTSVoices(language?: string): Promise<TTSVoice[]> {
  const url = language
    ? `${BASE_URL}/api/tts/voices?language=${encodeURIComponent(language)}`
    : `${BASE_URL}/api/tts/voices`;
  const res = await fetch(url);
  return handleResponse<TTSVoice[]>(res);
}

export async function generateAudioFromContent(
  projectId: string,
  data: TTSGenerateFromContentRequest,
): Promise<AudioRecord> {
  const res = await fetch(`${BASE_URL}/api/tts/generate-from-content/${projectId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return handleResponse<AudioRecord>(res);
}

export async function getAudioRecord(audioId: string): Promise<AudioRecord> {
  const res = await fetch(`${BASE_URL}/api/tts/${audioId}`);
  return handleResponse<AudioRecord>(res);
}

export async function deleteAudioRecord(audioId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/tts/${audioId}`, { method: 'DELETE' });
  return handleResponse<void>(res);
}

export function getAudioStreamUrl(audioId: string): string {
  return `${BASE_URL}/api/tts/${audioId}/audio`;
}
