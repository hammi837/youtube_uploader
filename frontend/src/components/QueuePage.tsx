import { useEffect, useRef, useState } from 'react';
import {
  cancelQueueJob,
  createQueueJobs,
  deleteQueueJob,
  getQueueStatus,
  listQueueJobs,
  pauseQueue,
  resumeQueue,
  retryQueueJob,
  startQueue,
} from '../services/api';
import type { QueueJob, QueueStatusSummary } from '../types/api';

const POLL_MS = 3000;

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_LABEL: Record<string, string> = {
  queued:             '⏸ Queued',
  researching:        '🔍 Researching',
  generating_script:  '✍ Writing Script',
  generating_audio:   '🎙 Generating Audio',
  generating_video:   '🎬 Generating Video',
  uploading:          '⬆ Uploading',
  scheduled:          '📅 Scheduled',
  completed:          '✅ Completed',
  failed:             '❌ Failed',
  cancelled:          '🚫 Cancelled',
  paused:             '⏸ Paused',
};

const STATUS_COLOR: Record<string, string> = {
  queued:             'var(--color-muted)',
  researching:        'var(--color-info)',
  generating_script:  'var(--color-info)',
  generating_audio:   'var(--color-info)',
  generating_video:   'var(--color-info)',
  uploading:          'var(--color-info)',
  scheduled:          '#a78bfa',
  completed:          'var(--color-success)',
  failed:             'var(--color-error)',
  cancelled:          'var(--color-muted)',
  paused:             'var(--color-warning)',
};

function isActive(s: string) {
  return ['researching','generating_script','generating_audio','generating_video','uploading'].includes(s);
}

function fmt(dt: string | null): string {
  if (!dt) return '—';
  return new Date(dt).toLocaleString();
}

function fmtDur(s: number): string {
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m` : `${s}s`;
}

// ── QueuePage ─────────────────────────────────────────────────────────────────

type View = 'create' | 'queue';

export function QueuePage() {
  const [view, setView] = useState<View>('create');
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="queue-page">
      <div className="content-subnav">
        <button className={`nav-tab ${view === 'create' ? 'nav-tab--active' : ''}`}
          onClick={() => setView('create')}>
          ➕ Add Topics
        </button>
        <button className={`nav-tab ${view === 'queue' ? 'nav-tab--active' : ''}`}
          onClick={() => { setView('queue'); setRefreshKey(k => k + 1); }}>
          📦 Queue
        </button>
      </div>

      {view === 'create' && (
        <BulkTopicForm onSubmitted={() => { setView('queue'); setRefreshKey(k => k + 1); }} />
      )}
      {view === 'queue' && (
        <QueueDashboard key={refreshKey} />
      )}
    </div>
  );
}

// ── BulkTopicForm ─────────────────────────────────────────────────────────────

function BulkTopicForm({ onSubmitted }: { onSubmitted: () => void }) {
  const [topicsText, setTopicsText]     = useState('');
  const [language, setLanguage]         = useState('en');
  const [tone, setTone]                 = useState('engaging');
  const [duration, setDuration]         = useState(180);
  const [scenes, setScenes]             = useState(10);
  const [scheduleEnabled, setSchedule]  = useState(false);
  const [scheduleStart, setStart]       = useState('');
  const [interval, setInterval]         = useState(240);
  const [privacy, setPrivacy]           = useState('private');
  const [submitting, setSubmitting]     = useState(false);
  const [error, setError]               = useState('');
  const [preview, setPreview]           = useState<string[]>([]);

  const parsedTopics = topicsText
    .split('\n')
    .map(t => t.trim())
    .filter(t => t.length > 0);

  // Schedule preview
  useEffect(() => {
    if (!scheduleEnabled || !scheduleStart || parsedTopics.length === 0) {
      setPreview([]);
      return;
    }
    try {
      const base = new Date(scheduleStart);
      if (isNaN(base.getTime())) { setPreview([]); return; }
      setPreview(parsedTopics.map((t, i) => {
        const dt = new Date(base.getTime() + i * interval * 60000);
        return `${dt.toUTCString().replace(' GMT', ' UTC')} — ${t.substring(0, 40)}`;
      }));
    } catch { setPreview([]); }
  }, [scheduleEnabled, scheduleStart, interval, topicsText]);

  async function handleSubmit() {
    if (parsedTopics.length === 0) { setError('Enter at least one topic.'); return; }
    setError(''); setSubmitting(true);
    try {
      await createQueueJobs({
        topics: parsedTopics,
        language,
        tone,
        target_duration_seconds: duration,
        scene_count: scenes,
        schedule_start: scheduleEnabled && scheduleStart ? scheduleStart : undefined,
        schedule_interval_minutes: interval,
        youtube_privacy_status: privacy,
      });
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create queue jobs.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="queue-form">
      <h2 className="section-title">➕ Add Topics to Queue</h2>

      <div className="form-group">
        <label className="form-label" htmlFor="topics-input">
          Topics <span className="hint-text">(one per line, {parsedTopics.length} entered)</span>
        </label>
        <textarea
          id="topics-input"
          className="form-textarea"
          rows={8}
          placeholder={"Why do cats purr?\nHow do black holes work?\nWhy is the sky blue?\nHow do airplanes fly?"}
          value={topicsText}
          onChange={e => setTopicsText(e.target.value)}
          disabled={submitting}
        />
      </div>

      <div className="queue-form__options">
        <div className="form-group">
          <label className="form-label">Language</label>
          <select className="form-select" value={language}
            onChange={e => setLanguage(e.target.value)} disabled={submitting}>
            <option value="en">English</option>
            <option value="ur">Urdu</option>
            <option value="es">Spanish</option>
            <option value="fr">French</option>
            <option value="de">German</option>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Tone</label>
          <select className="form-select" value={tone}
            onChange={e => setTone(e.target.value)} disabled={submitting}>
            <option value="engaging">Engaging</option>
            <option value="informative">Informative</option>
            <option value="casual">Casual</option>
            <option value="professional">Professional</option>
            <option value="educational">Educational</option>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Duration (sec)</label>
          <select className="form-select" value={duration}
            onChange={e => setDuration(Number(e.target.value))} disabled={submitting}>
            <option value={120}>2 min</option>
            <option value={180}>3 min</option>
            <option value={240}>4 min</option>
            <option value={300}>5 min</option>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">Scenes</label>
          <select className="form-select" value={scenes}
            onChange={e => setScenes(Number(e.target.value))} disabled={submitting}>
            <option value={7}>7</option>
            <option value={10}>10</option>
            <option value={12}>12</option>
            <option value={15}>15</option>
          </select>
        </div>
        <div className="form-group">
          <label className="form-label">YouTube Privacy</label>
          <select className="form-select" value={privacy}
            onChange={e => setPrivacy(e.target.value)} disabled={submitting}>
            <option value="private">Private</option>
            <option value="unlisted">Unlisted</option>
            <option value="public">Public</option>
          </select>
        </div>
      </div>

      {/* Scheduling */}
      <div className="queue-schedule-toggle">
        <label className="checkbox-label">
          <input type="checkbox" checked={scheduleEnabled}
            onChange={e => setSchedule(e.target.checked)} disabled={submitting} />
          <span>📅 Schedule YouTube publish times automatically</span>
        </label>
      </div>

      {scheduleEnabled && (
        <div className="queue-schedule-fields">
          <div className="form-group">
            <label className="form-label">First video publish time (UTC or with offset)</label>
            <input
              type="datetime-local"
              className="form-input"
              value={scheduleStart}
              onChange={e => setStart(e.target.value + ':00+00:00')}
              disabled={submitting}
            />
            <div className="form-hint form-hint--info">
              Use a time at least 15 minutes in the future. Videos upload as private and publish at scheduled time.
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Interval between videos</label>
            <select className="form-select" value={interval}
              onChange={e => setInterval(Number(e.target.value))} disabled={submitting}>
              <option value={30}>30 minutes</option>
              <option value={60}>1 hour</option>
              <option value={120}>2 hours</option>
              <option value={240}>4 hours</option>
              <option value={360}>6 hours</option>
              <option value={720}>12 hours</option>
              <option value={1440}>24 hours</option>
            </select>
          </div>

          {preview.length > 0 && (
            <div className="queue-schedule-preview">
              <div className="detail-label" style={{ marginBottom: '0.4rem' }}>📅 Schedule Preview</div>
              {preview.map((p, i) => (
                <div key={i} className="queue-schedule-preview__item">{p}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="alert alert--error" role="alert" style={{ marginTop: '1rem' }}>
          {error}
        </div>
      )}

      <div style={{ marginTop: '1rem', display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
        <button className="btn btn--primary" onClick={handleSubmit} disabled={submitting || parsedTopics.length === 0}>
          {submitting
            ? <><span className="tts-spinner" aria-hidden="true" /> Adding to queue…</>
            : `📦 Add ${parsedTopics.length} topic${parsedTopics.length !== 1 ? 's' : ''} to queue`}
        </button>
        <span className="hint-text">Processing one video at a time (i5-6300U CPU)</span>
      </div>
    </div>
  );
}

// ── QueueDashboard ────────────────────────────────────────────────────────────

function QueueDashboard() {
  const [jobs, setJobs]           = useState<QueueJob[]>([]);
  const [qstat, setQstat]         = useState<QueueStatusSummary | null>(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState('');
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    load();
    pollingRef.current = setInterval(load, POLL_MS);
    return () => { if (pollingRef.current) clearInterval(pollingRef.current); };
  }, []);

  async function load() {
    try {
      const [j, s] = await Promise.all([listQueueJobs(), getQueueStatus()]);
      setJobs(j);
      setQstat(s);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load queue.');
    } finally {
      setLoading(false);
    }
  }

  async function act(fn: () => Promise<unknown>, afterMsg?: string) {
    try { await fn(); await load(); }
    catch (err) { alert(err instanceof Error ? err.message : afterMsg ?? 'Error'); }
  }

  if (loading) return <p className="hint-text">Loading queue…</p>;
  if (error)   return <div className="alert alert--error">{error}</div>;

  return (
    <div className="queue-dashboard">

      {/* Stats */}
      {qstat && (
        <div className="queue-stats">
          <div className="queue-stats__grid">
            <div className="queue-stat"><span className="queue-stat__num">{qstat.total}</span><span>Total</span></div>
            <div className="queue-stat"><span className="queue-stat__num" style={{color:'var(--color-info)'}}>{qstat.queued}</span><span>Queued</span></div>
            <div className="queue-stat"><span className="queue-stat__num" style={{color:'var(--color-warning)'}}>{qstat.processing}</span><span>Processing</span></div>
            <div className="queue-stat"><span className="queue-stat__num" style={{color:'var(--color-success)'}}>{qstat.completed}</span><span>Completed</span></div>
            <div className="queue-stat"><span className="queue-stat__num" style={{color:'var(--color-error)'}}>{qstat.failed}</span><span>Failed</span></div>
          </div>

          {/* Controls */}
          <div className="queue-controls">
            {!qstat.queue_running && (
              <button className="btn btn--primary btn--sm"
                onClick={() => act(() => startQueue(), 'Start failed')}>
                ▶ Start Queue
              </button>
            )}
            {qstat.queue_running && !qstat.queue_paused && (
              <button className="btn btn--ghost btn--sm"
                onClick={() => act(() => pauseQueue(), 'Pause failed')}>
                ⏸ Pause
              </button>
            )}
            {qstat.queue_paused && (
              <button className="btn btn--primary btn--sm"
                onClick={() => act(() => resumeQueue(), 'Resume failed')}>
                ▶ Resume
              </button>
            )}
            <span className={`queue-worker-badge ${qstat.queue_running ? 'queue-worker-badge--on' : 'queue-worker-badge--off'}`}>
              {qstat.queue_running
                ? qstat.queue_paused ? '⏸ Paused' : '● Running'
                : '○ Stopped'}
            </span>
          </div>
        </div>
      )}

      {jobs.length === 0 ? (
        <div className="alert alert--info" style={{ marginTop: '1rem' }}>
          No queue jobs yet. Add topics in the ➕ Add Topics tab.
        </div>
      ) : (
        <div className="queue-list">
          {jobs.map(job => (
            <QueueJobCard
              key={job.id}
              job={job}
              isCurrent={qstat?.current_job_id === job.id}
              onRefresh={load}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── QueueJobCard ──────────────────────────────────────────────────────────────

function QueueJobCard({
  job,
  isCurrent,
  onRefresh,
}: {
  job: QueueJob;
  isCurrent: boolean;
  onRefresh: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try { await fn(); onRefresh(); }
    catch (err) { alert(err instanceof Error ? err.message : 'Error'); }
    finally { setBusy(false); }
  }

  const active = isActive(job.status);
  const color  = STATUS_COLOR[job.status] ?? 'var(--color-muted)';

  return (
    <div className={`queue-job-card ${isCurrent ? 'queue-job-card--current' : ''}`}>
      <div className="queue-job-card__header">
        <div className="queue-job-card__topic" title={job.topic}>
          {job.topic.length > 70 ? job.topic.substring(0, 67) + '…' : job.topic}
        </div>
        <span className="queue-job-card__status" style={{ color }}>
          {STATUS_LABEL[job.status] ?? job.status}
        </span>
      </div>

      {/* Progress bar for active jobs */}
      {active && (
        <div style={{ margin: '0.4rem 0' }}>
          <div className="video-progress-bar-outer" style={{ height: '6px' }}>
            <div className="video-progress-bar-inner" style={{ width: `${job.progress}%` }} />
          </div>
          <div className="hint-text" style={{ fontSize: '0.78rem', marginTop: '2px' }}>
            {job.current_stage}  {job.progress}%
          </div>
        </div>
      )}

      {/* Metadata row */}
      <div className="queue-job-card__meta">
        <span>{fmtDur(job.target_duration_seconds)}</span>
        <span>{job.language.toUpperCase()}</span>
        <span>{job.tone}</span>
        {job.retry_count > 0 && (
          <span style={{ color: 'var(--color-warning)' }}>
            Retry {job.retry_count}/{job.max_retries}
          </span>
        )}
        {job.scheduled_publish_at && (
          <span style={{ color: '#a78bfa' }}>
            📅 {fmt(job.scheduled_publish_at)}
          </span>
        )}
      </div>

      {/* YouTube info */}
      {job.youtube_url && (
        <div className="queue-job-card__yt">
          <a href={job.youtube_url} target="_blank" rel="noreferrer"
            className="btn btn--ghost btn--sm">
            ▶ YouTube
          </a>
          {job.thumbnail_uploaded && <span className="hint-text">🖼 Thumb ✓</span>}
          {job.schedule_set && <span className="hint-text">📅 Scheduled ✓</span>}
        </div>
      )}

      {/* Error */}
      {job.status === 'failed' && job.error_message && (
        <div className="queue-job-error">
          {job.error_message.substring(0, 200)}
        </div>
      )}

      {/* Timestamps */}
      <div className="hint-text" style={{ fontSize: '0.75rem', marginTop: '0.25rem' }}>
        Created {fmt(job.created_at)}
        {job.completed_at && ` · Completed ${fmt(job.completed_at)}`}
      </div>

      {/* Actions */}
      <div className="queue-job-card__actions">
        {job.status === 'failed' && (
          <button className="btn btn--ghost btn--sm" disabled={busy}
            onClick={() => act(() => retryQueueJob(job.id))}>
            ↺ Retry
          </button>
        )}
        {job.status === 'queued' && (
          <button className="btn btn--ghost btn--sm" disabled={busy}
            onClick={() => act(() => cancelQueueJob(job.id))}>
            🚫 Cancel
          </button>
        )}
        {['completed', 'cancelled', 'failed'].includes(job.status) && (
          <button className="btn btn--danger btn--sm" disabled={busy}
            onClick={() => {
              if (window.confirm('Delete this queue job record?'))
                act(() => deleteQueueJob(job.id));
            }}>
            🗑 Delete
          </button>
        )}
      </div>
    </div>
  );
}
