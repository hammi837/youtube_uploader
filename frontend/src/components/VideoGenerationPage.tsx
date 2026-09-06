import { useEffect, useRef, useState } from 'react';
import {
  createVideoJob,
  deleteVideoJob,
  getContentProjects,
  getVideoJob,
  getVideoCaptionUrl,
  getVideoStreamUrl,
  getVideoThumbnailUrl,
  listVideoJobs,
} from '../services/api';
import type { ContentProjectSummary, VideoJob } from '../types/api';

const POLL_MS = 2500;

type PageView = 'create' | 'history';

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS_LABEL: Record<string, string> = {
  queued:               'Queued',
  preparing:            'Preparing…',
  generating_audio:     'Generating narration…',
  preparing_visuals:    'Building visuals…',
  generating_captions:  'Generating captions…',
  assembling:           'Assembling video…',
  generating_thumbnail: 'Generating thumbnail…',
  completed:            'Complete',
  failed:               'Failed',
  cancelled:            'Cancelled',
};

const STEPS = [
  { key: 'queued',               label: 'Job queued',            pct: 0   },
  { key: 'preparing',            label: 'Preparing',             pct: 5   },
  { key: 'generating_audio',     label: 'Narration audio',       pct: 10  },
  { key: 'preparing_visuals',    label: 'Scene visuals',         pct: 30  },
  { key: 'generating_captions',  label: 'Captions',              pct: 45  },
  { key: 'assembling',           label: 'Video assembly',        pct: 55  },
  { key: 'generating_thumbnail', label: 'Thumbnail',             pct: 90  },
  { key: 'completed',            label: 'Done',                  pct: 100 },
];

function stepIndex(status: string): number {
  return STEPS.findIndex((s) => s.key === status);
}

function formatBytes(b: number | null): string {
  if (b === null) return '—';
  if (b >= 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`;
  return `${(b / 1024).toFixed(0)} KB`;
}

function formatDur(s: number | null): string {
  if (s === null) return '—';
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

// ── Main component ────────────────────────────────────────────────────────────

export function VideoGenerationPage() {
  const [view, setView] = useState<PageView>('create');
  const [historyKey, setHistoryKey] = useState(0);

  return (
    <div className="video-gen-page">
      <div className="content-subnav">
        <button
          className={`nav-tab ${view === 'create' ? 'nav-tab--active' : ''}`}
          onClick={() => setView('create')}
        >
          🎬 Generate Video
        </button>
        <button
          className={`nav-tab ${view === 'history' ? 'nav-tab--active' : ''}`}
          onClick={() => { setView('history'); setHistoryKey((k) => k + 1); }}
        >
          📼 Video History
        </button>
      </div>

      {view === 'create' && (
        <VideoCreatePanel onJobCreated={() => { setView('history'); setHistoryKey((k) => k + 1); }} />
      )}
      {view === 'history' && (
        <VideoHistoryPanel key={historyKey} />
      )}
    </div>
  );
}

// ── Create panel ──────────────────────────────────────────────────────────────

function VideoCreatePanel({ onJobCreated: _onJobCreated }: { onJobCreated: () => void }) {
  const [projects, setProjects]         = useState<ContentProjectSummary[]>([]);
  const [loadingProjects, setLoading]   = useState(true);
  const [selectedId, setSelectedId]     = useState('');
  const [captionsEnabled, setCaptions]  = useState(true);
  const [musicEnabled, setMusic]        = useState(true);
  const [job, setJob]                   = useState<VideoJob | null>(null);
  const [error, setError]               = useState('');
  const [generating, setGenerating]     = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getContentProjects()
      .then((ps) => {
        const ready = ps.filter((p) => p.has_script && p.status === 'completed');
        setProjects(ready);
        if (ready.length > 0) setSelectedId(ready[0].id);
      })
      .catch(() => setError('Could not load content projects.'))
      .finally(() => setLoading(false));
    return () => stopPolling();
  }, []);

  function stopPolling() {
    if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
  }

  function startPolling(jobId: string) {
    stopPolling();
    pollingRef.current = setInterval(async () => {
      try {
        const updated = await getVideoJob(jobId);
        setJob(updated);
        if (updated.status === 'completed' || updated.status === 'failed') {
          stopPolling();
          setGenerating(false);
        }
      } catch { /* network blip — keep polling */ }
    }, POLL_MS);
  }

  async function handleGenerate() {
    if (!selectedId) return;
    setError('');
    setJob(null);
    setGenerating(true);
    try {
      const created = await createVideoJob(selectedId, {
        captions_enabled: captionsEnabled,
        music_enabled: musicEnabled,
      });
      setJob(created);
      startPolling(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start video generation.');
      setGenerating(false);
    }
  }

  const selected = projects.find((p) => p.id === selectedId);
  const isComplete = job?.status === 'completed';
  const isFailed   = job?.status === 'failed';

  return (
    <div className="video-create-panel">
      <h2 className="section-title">🎬 Generate Video</h2>

      {loadingProjects ? (
        <p className="hint-text">Loading content projects…</p>
      ) : projects.length === 0 ? (
        <div className="alert alert--info">
          No completed content projects found. Generate a script in the <strong>AI Content</strong> tab first.
        </div>
      ) : (
        <>
          {/* Project selector */}
          <div className="form-group">
            <label className="form-label" htmlFor="project-select">Content Project</label>
            <select
              id="project-select"
              className="form-select"
              value={selectedId}
              disabled={generating}
              onChange={(e) => setSelectedId(e.target.value)}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.topic}</option>
              ))}
            </select>
          </div>

          {/* Selected project info */}
          {selected && (
            <div className="video-project-info">
              <span className="detail-label">Duration target</span>
              <span className="detail-value">{formatDur(selected.target_duration_seconds)}</span>
              <span className="detail-label">Scenes</span>
              <span className="detail-value">{selected.scene_count}</span>
              <span className="detail-label">Language</span>
              <span className="detail-value">{selected.language.toUpperCase()}</span>
            </div>
          )}

          {/* Options */}
          <div className="video-options">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={captionsEnabled}
                disabled={generating}
                onChange={(e) => setCaptions(e.target.checked)}
              />
              <span>Burn captions into video</span>
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={musicEnabled}
                disabled={generating}
                onChange={(e) => setMusic(e.target.checked)}
              />
              <span>Background music (if available in data/assets/music/)</span>
            </label>
          </div>

          <div className="form-hint form-hint--info" style={{ marginBottom: '1rem' }}>
            Output: 1920×1080 · 30fps · H.264/AAC · MP4
          </div>

          {/* Generate button */}
          {!isComplete && (
            <button
              className="btn btn--primary"
              onClick={handleGenerate}
              disabled={generating || !selectedId}
            >
              {generating ? (
                <><span className="tts-spinner" aria-hidden="true" /> Generating…</>
              ) : '🎬 Generate Video'}
            </button>
          )}
        </>
      )}

      {/* Error */}
      {error && (
        <div className="alert alert--error" role="alert" style={{ marginTop: '1rem' }}>
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* Progress tracker */}
      {job && !isFailed && (
        <div className="video-progress-panel" role="status" aria-live="polite">
          <div className="video-progress-header">
            <span>{STATUS_LABEL[job.status] ?? job.status}</span>
            <span className="video-progress-pct">{job.progress}%</span>
          </div>

          <div className="video-progress-bar-outer">
            <div
              className="video-progress-bar-inner"
              style={{ width: `${job.progress}%` }}
              aria-valuenow={job.progress}
              aria-valuemin={0}
              aria-valuemax={100}
              role="progressbar"
            />
          </div>

          {job.current_step && (
            <div className="video-current-step">{job.current_step}</div>
          )}

          {/* Step checklist */}
          <div className="video-steps">
            {STEPS.filter((s) => s.key !== 'queued').map((s) => {
              const done    = stepIndex(job.status) > STEPS.findIndex((x) => x.key === s.key);
              const active  = job.status === s.key;
              return (
                <div
                  key={s.key}
                  className={`video-step ${done ? 'video-step--done' : ''} ${active ? 'video-step--active' : ''}`}
                >
                  <span className="video-step__icon">
                    {done ? '✓' : active ? '⟳' : '○'}
                  </span>
                  <span>{s.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Failure */}
      {isFailed && job && (
        <div className="alert alert--error" role="alert" style={{ marginTop: '1rem' }}>
          <strong>❌ Video generation failed</strong>
          <br />
          <span style={{ opacity: 0.85 }}>{job.error_message ?? 'An unexpected error occurred.'}</span>
          <div style={{ marginTop: '0.5rem' }}>
            <button className="btn btn--ghost btn--sm" onClick={() => { setJob(null); setGenerating(false); }}>
              Try again
            </button>
          </div>
        </div>
      )}

      {/* Completed result */}
      {isComplete && job && (
        <div className="video-result">
          <div className="video-result__header">
            ✅ Video Ready
          </div>

          {/* Thumbnail */}
          {job.thumbnail_url && (
            <div className="video-result__thumb">
              <img
                src={job.thumbnail_url}
                alt="Video thumbnail"
                className="video-thumbnail-img"
              />
            </div>
          )}

          {/* Metadata */}
          <div className="video-result__meta">
            <div className="tts-result__item">
              <span className="detail-label">Duration</span>
              <span className="detail-value">{formatDur(job.duration_seconds)}</span>
            </div>
            <div className="tts-result__item">
              <span className="detail-label">File size</span>
              <span className="detail-value">{formatBytes(job.file_size_bytes)}</span>
            </div>
            <div className="tts-result__item">
              <span className="detail-label">Resolution</span>
              <span className="detail-value">{job.width}×{job.height} @ {job.fps}fps</span>
            </div>
            <div className="tts-result__item">
              <span className="detail-label">Captions</span>
              <span className="detail-value">{job.captions_enabled ? '✓ Burned in' : 'Off'}</span>
            </div>
            <div className="tts-result__item">
              <span className="detail-label">Music</span>
              <span className="detail-value">{job.music_enabled ? '✓ Mixed' : 'Off'}</span>
            </div>
          </div>

          {/* Video player */}
          {job.video_url && (
            <div className="video-player-wrap">
              <video
                controls
                src={job.video_url}
                className="video-player"
                aria-label="Generated video"
                style={{ width: '100%', maxHeight: '480px', borderRadius: '8px' }}
              >
                Your browser does not support the video element.
              </video>
            </div>
          )}

          {/* Actions */}
          <div className="tts-result__actions" style={{ marginTop: '1rem' }}>
            {job.video_url && (
              <a href={getVideoStreamUrl(job.id)} download={`video_${job.id}.mp4`}
                className="btn btn--ghost btn--sm">
                📥 Download MP4
              </a>
            )}
            {job.thumbnail_url && (
              <a href={getVideoThumbnailUrl(job.id)} download={`thumb_${job.id}.jpg`}
                className="btn btn--ghost btn--sm">
                🖼 Download Thumbnail
              </a>
            )}
            {job.caption_url && (
              <a href={getVideoCaptionUrl(job.id)} download={`captions_${job.id}.srt`}
                className="btn btn--ghost btn--sm">
                📄 Download Captions
              </a>
            )}
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => { setJob(null); setGenerating(false); }}
            >
              ↺ Generate Another
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── History panel ─────────────────────────────────────────────────────────────

function VideoHistoryPanel() {
  const [jobs, setJobs]       = useState<VideoJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState('');
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    listVideoJobs()
      .then(setJobs)
      .catch(() => setError('Could not load video history.'))
      .finally(() => setLoading(false));
  }, []);

  async function handleDelete(jobId: string) {
    if (!window.confirm('Delete this video job and its output files?')) return;
    setDeleting(jobId);
    try {
      await deleteVideoJob(jobId);
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed.');
    } finally {
      setDeleting(null);
    }
  }

  if (loading) return <p className="hint-text">Loading video history…</p>;
  if (error)   return <div className="alert alert--error">{error}</div>;
  if (jobs.length === 0) return (
    <div className="alert alert--info">No video generation jobs yet. Generate your first video above.</div>
  );

  return (
    <div className="video-history">
      <h2 className="section-title">📼 Video History</h2>
      {jobs.map((job) => (
        <div key={job.id} className={`video-history-card video-history-card--${job.status}`}>
          <div className="video-history-card__header">
            <div className="video-history-card__meta">
              <span className={`status-badge status-badge--${job.status}`}>
                {STATUS_LABEL[job.status] ?? job.status}
              </span>
              <span className="hint-text" style={{ fontSize: '0.8rem' }}>
                {new Date(job.created_at).toLocaleString()}
              </span>
            </div>
            <div className="video-history-card__stats">
              {job.duration_seconds && <span>{formatDur(job.duration_seconds)}</span>}
              {job.file_size_bytes && <span>{formatBytes(job.file_size_bytes)}</span>}
              <span>{job.width}×{job.height}</span>
            </div>
          </div>

          {job.status !== 'completed' && job.status !== 'failed' && (
            <div style={{ margin: '0.5rem 0' }}>
              <div className="video-progress-bar-outer" style={{ height: '6px' }}>
                <div className="video-progress-bar-inner" style={{ width: `${job.progress}%` }} />
              </div>
              <div className="hint-text" style={{ fontSize: '0.8rem', marginTop: '2px' }}>
                {job.current_step}  {job.progress}%
              </div>
            </div>
          )}

          {job.status === 'failed' && job.error_message && (
            <div className="alert alert--error" style={{ marginTop: '0.5rem', padding: '0.5rem' }}>
              {job.error_message}
            </div>
          )}

          {job.status === 'completed' && (
            <div className="video-history-card__actions">
              {job.video_url && (
                <a href={job.video_url} className="btn btn--ghost btn--sm" target="_blank" rel="noreferrer">
                  ▶ Play
                </a>
              )}
              {job.video_url && (
                <a href={getVideoStreamUrl(job.id)} download className="btn btn--ghost btn--sm">
                  📥 MP4
                </a>
              )}
              {job.thumbnail_url && (
                <a href={getVideoThumbnailUrl(job.id)} download className="btn btn--ghost btn--sm">
                  🖼 Thumb
                </a>
              )}
              {job.caption_url && (
                <a href={getVideoCaptionUrl(job.id)} download className="btn btn--ghost btn--sm">
                  📄 SRT
                </a>
              )}
            </div>
          )}

          <button
            className="btn btn--danger btn--sm"
            style={{ marginTop: '0.5rem' }}
            onClick={() => handleDelete(job.id)}
            disabled={deleting === job.id}
          >
            {deleting === job.id ? 'Deleting…' : '🗑 Delete'}
          </button>
        </div>
      ))}
    </div>
  );
}
