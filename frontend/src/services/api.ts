import type {
  AudioRecord,
  AuthStatus,
  BulkQueueRequest,
  BulkQueueResponse,
  CleanupResult,
  ContentProjectDetail,
  ContentProjectSummary,
  HealthResponse,
  JobLogEntry,
  QueueHealth,
  QueueJob,
  QueueStats,
  QueueStatusSummary,
  ResearchRequest,
  ResearchResponse,
  ScriptRequest,
  TTSGenerateFromContentRequest,
  TTSVoice,
  UploadJob,
  VideoGenerationRequest,
  VideoJob,
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

// ── Phase 2D: Video Generation ───────────────────────────────────────────────

export async function createVideoJob(
  projectId: string,
  data: VideoGenerationRequest,
): Promise<VideoJob> {
  const res = await fetch(`${BASE_URL}/api/video-generation/from-content/${projectId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return handleResponse<VideoJob>(res);
}

export async function getVideoJob(jobId: string): Promise<VideoJob> {
  const res = await fetch(`${BASE_URL}/api/video-generation/${jobId}`);
  return handleResponse<VideoJob>(res);
}

export async function listVideoJobs(): Promise<VideoJob[]> {
  const res = await fetch(`${BASE_URL}/api/video-generation`);
  return handleResponse<VideoJob[]>(res);
}

export async function deleteVideoJob(jobId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/video-generation/${jobId}`, { method: 'DELETE' });
  return handleResponse<void>(res);
}

export function getVideoStreamUrl(jobId: string): string {
  return `${BASE_URL}/api/video-generation/${jobId}/video`;
}

export function getVideoThumbnailUrl(jobId: string): string {
  return `${BASE_URL}/api/video-generation/${jobId}/thumbnail`;
}

export function getVideoCaptionUrl(jobId: string): string {
  return `${BASE_URL}/api/video-generation/${jobId}/captions`;
}

// ── Phase 3A: Content Queue ───────────────────────────────────────────────────

export async function createQueueJobs(data: BulkQueueRequest): Promise<BulkQueueResponse> {
  const res = await fetch(`${BASE_URL}/api/queue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return handleResponse<BulkQueueResponse>(res);
}

export async function listQueueJobs(statusFilter?: string): Promise<QueueJob[]> {
  const url = statusFilter
    ? `${BASE_URL}/api/queue?status=${encodeURIComponent(statusFilter)}`
    : `${BASE_URL}/api/queue`;
  const res = await fetch(url);
  return handleResponse<QueueJob[]>(res);
}

export async function getQueueJob(jobId: string): Promise<QueueJob> {
  const res = await fetch(`${BASE_URL}/api/queue/${jobId}`);
  return handleResponse<QueueJob>(res);
}

export async function getQueueStatus(): Promise<QueueStatusSummary> {
  const res = await fetch(`${BASE_URL}/api/queue/status`);
  return handleResponse<QueueStatusSummary>(res);
}

export async function cancelQueueJob(jobId: string): Promise<QueueJob> {
  const res = await fetch(`${BASE_URL}/api/queue/${jobId}/cancel`, { method: 'POST' });
  return handleResponse<QueueJob>(res);
}

export async function retryQueueJob(jobId: string): Promise<QueueJob> {
  const res = await fetch(`${BASE_URL}/api/queue/${jobId}/retry`, { method: 'POST' });
  return handleResponse<QueueJob>(res);
}

export async function deleteQueueJob(jobId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/queue/${jobId}`, { method: 'DELETE' });
  return handleResponse<void>(res);
}

export async function pauseQueue(): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/queue/pause`, { method: 'POST' });
  return handleResponse<void>(res);
}

export async function resumeQueue(): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/queue/resume`, { method: 'POST' });
  return handleResponse<void>(res);
}

export async function startQueue(): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/queue/start`, { method: 'POST' });
  return handleResponse<void>(res);
}

// ── Phase 3B: Queue additional endpoints ─────────────────────────────────────

export async function stopQueue(): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/queue/stop`, { method: 'POST' });
  return handleResponse<void>(res);
}

export async function retryAllFailed(): Promise<{ count: number; message: string }> {
  const res = await fetch(`${BASE_URL}/api/queue/retry-failed`, { method: 'POST' });
  return handleResponse<{ count: number; message: string }>(res);
}

export async function clearCompleted(): Promise<{ count: number; message: string }> {
  const res = await fetch(`${BASE_URL}/api/queue/clear-completed`, { method: 'POST' });
  return handleResponse<{ count: number; message: string }>(res);
}

export async function clearFailed(): Promise<{ count: number; message: string }> {
  const res = await fetch(`${BASE_URL}/api/queue/clear-failed`, { method: 'POST' });
  return handleResponse<{ count: number; message: string }>(res);
}

export async function runCleanup(dryRun = false): Promise<CleanupResult> {
  const res = await fetch(`${BASE_URL}/api/queue/cleanup?dry_run=${dryRun}`, { method: 'POST' });
  return handleResponse<CleanupResult>(res);
}

export async function getQueueHealth(): Promise<QueueHealth> {
  const res = await fetch(`${BASE_URL}/api/queue/health`);
  return handleResponse<QueueHealth>(res);
}

export async function getQueueStats(): Promise<QueueStats> {
  const res = await fetch(`${BASE_URL}/api/queue/stats`);
  return handleResponse<QueueStats>(res);
}

export async function getJobLogs(jobId: string, limit = 100): Promise<JobLogEntry[]> {
  const res = await fetch(`${BASE_URL}/api/queue/${jobId}/logs?limit=${limit}`);
  return handleResponse<JobLogEntry[]>(res);
}
