import { useEffect, useRef, useState } from 'react';
import {
  cancelQueueJob,
  clearCompleted,
  clearFailed,
  createQueueJobs,
  deleteQueueJob,
  getJobLogs,
  getQueueHealth,
  getQueueStats,
  getQueueStatus,
  listQueueJobs,
  pauseQueue,
  resumeQueue,
  retryAllFailed,
  retryQueueJob,
  runCleanup,
  startQueue,
  stopQueue,
} from '../services/api';
import type {
  CleanupResult,
  JobLogEntry,
  QueueHealth,
  QueueJob,
  QueueStats,
} from '../types/api';

const POLL_MS = 2000;

const STATUS_LABEL: Record<string, string> = {
  queued:            '⏸ Queued',
  researching:       '🔍 Researching',
  generating_script: '✍ Script',
  generating_audio:  '🎙 Audio',
  generating_video:  '🎬 Video',
  uploading:         '⬆ Uploading',
  scheduled:         '📅 Scheduled',
  completed:         '✅ Completed',
  failed:            '❌ Failed',
  cancelled:         '🚫 Cancelled',
  paused:            '⏸ Paused',
};

const STATUS_COLOR: Record<string, string> = {
  queued:            'var(--color-muted)',
  researching:       'var(--color-info)',
  generating_script: 'var(--color-info)',
  generating_audio:  'var(--color-info)',
  generating_video:  'var(--color-info)',
  uploading:         'var(--color-info)',
  scheduled:         '#a78bfa',
  completed:         'var(--color-success)',
  failed:            'var(--color-error)',
  cancelled:         'var(--color-muted)',
  paused:            'var(--color-warning)',
};

const ACTIVE_STATUSES = new Set([
  'researching','generating_script','generating_audio','generating_video','uploading'
]);

function isActive(s: string) { return ACTIVE_STATUSES.has(s); }

function fmtBytes(b: number): string {
  if (b >= 1024*1024*1024) return `${(b/1024/1024/1024).toFixed(1)} GB`;
  if (b >= 1024*1024) return `${(b/1024/1024).toFixed(1)} MB`;
  return `${(b/1024).toFixed(0)} KB`;
}
function fmtDur(s: number): string {
  const m = Math.floor(s / 60); return m > 0 ? `${m}m` : `${s}s`;
}
function fmt(dt: string | null): string {
  if (!dt) return '—';
  return new Date(dt).toLocaleString();
}
function fmtNow(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

// ── QueuePage ─────────────────────────────────────────────────────────────────

type View = 'create' | 'queue' | 'logs';

export function QueuePage() {
  const [view, setView] = useState<View>('create');
  const [refreshKey, setRefreshKey] = useState(0);
  const [logsJobId, setLogsJobId] = useState<string | null>(null);

  function openLogs(jobId: string) {
    setLogsJobId(jobId);
    setView('logs');
  }

  return (
    <div className="queue-page">
      <div className="content-subnav">
        <button className={`nav-tab ${view === 'create' ? 'nav-tab--active' : ''}`}
          onClick={() => setView('create')}>➕ Add Topics</button>
        <button className={`nav-tab ${view === 'queue' ? 'nav-tab--active' : ''}`}
          onClick={() => { setView('queue'); setRefreshKey(k => k+1); }}>📦 Queue</button>
        {logsJobId && (
          <button className={`nav-tab ${view === 'logs' ? 'nav-tab--active' : ''}`}
            onClick={() => setView('logs')}>📋 Job Logs</button>
        )}
      </div>

      {view === 'create' && (
        <BulkTopicForm onSubmitted={() => { setView('queue'); setRefreshKey(k => k+1); }} />
      )}
      {view === 'queue' && (
        <QueueDashboard key={refreshKey} onOpenLogs={openLogs} />
      )}
      {view === 'logs' && logsJobId && (
        <JobLogsPanel jobId={logsJobId} onBack={() => setView('queue')} />
      )}
    </div>
  );
}

// ── BulkTopicForm ─────────────────────────────────────────────────────────────

function BulkTopicForm({ onSubmitted }: { onSubmitted: () => void }) {
  const [topicsText, setTopicsText] = useState('');
  const [language, setLanguage]     = useState('en');
  const [tone, setTone]             = useState('engaging');
  const [duration, setDuration]     = useState(180);
  const [scenes, setScenes]         = useState(10);
  const [scheduleEnabled, setSched] = useState(false);
  const [scheduleStart, setStart]   = useState('');
  const [interval, setInterval]     = useState(240);
  const [privacy, setPrivacy]       = useState('private');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError]           = useState('');
  const [preview, setPreview]       = useState<string[]>([]);

  const parsedTopics = topicsText.split('\n').map(t=>t.trim()).filter(t=>t.length>0);

  useEffect(() => {
    if (!scheduleEnabled || !scheduleStart || parsedTopics.length === 0) { setPreview([]); return; }
    try {
      const base = new Date(scheduleStart);
      if (isNaN(base.getTime())) { setPreview([]); return; }
      setPreview(parsedTopics.map((t, i) => {
        const dt = new Date(base.getTime() + i * interval * 60000);
        return `${dt.toUTCString().replace(' GMT',' UTC')} — ${t.substring(0,40)}`;
      }));
    } catch { setPreview([]); }
  }, [scheduleEnabled, scheduleStart, interval, topicsText]);

  async function handleSubmit() {
    if (parsedTopics.length === 0) { setError('Enter at least one topic.'); return; }
    setError(''); setSubmitting(true);
    try {
      await createQueueJobs({
        topics: parsedTopics, language, tone,
        target_duration_seconds: duration, scene_count: scenes,
        schedule_start: scheduleEnabled && scheduleStart ? scheduleStart : undefined,
        schedule_interval_minutes: interval,
        youtube_privacy_status: privacy,
      });
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create queue jobs.');
    } finally { setSubmitting(false); }
  }

  return (
    <div className="queue-form">
      <h2 className="section-title">➕ Add Topics to Queue</h2>

      <div className="form-group">
        <label className="form-label" htmlFor="topics-input">
          Topics <span className="hint-text">({parsedTopics.length} entered — one per line)</span>
        </label>
        <textarea id="topics-input" className="form-textarea" rows={7}
          placeholder={"Why do cats purr?\nHow do black holes work?\nWhy is the sky blue?"}
          value={topicsText} onChange={e => setTopicsText(e.target.value)}
          disabled={submitting} />
      </div>

      <div className="queue-form__options">
        {[
          ['Language','language',language,setLanguage,[['en','English'],['ur','Urdu'],['es','Spanish'],['fr','French'],['de','German']]],
          ['Tone','tone',tone,setTone,[['engaging','Engaging'],['informative','Informative'],['casual','Casual'],['educational','Educational']]],
          ['Duration','duration',duration,setDuration,[[120,'2 min'],[180,'3 min'],[240,'4 min'],[300,'5 min']]],
          ['Scenes','scenes',scenes,setScenes,[[7,'7'],[10,'10'],[12,'12'],[15,'15']]],
          ['Privacy','privacy',privacy,setPrivacy,[['private','Private'],['unlisted','Unlisted'],['public','Public']]],
        ].map(([label, , val, setter, opts]: any) => (
          <div className="form-group" key={label}>
            <label className="form-label">{label}</label>
            <select className="form-select" value={val}
              onChange={e => setter(e.target.value === String(Number(e.target.value)) ? Number(e.target.value) : e.target.value)}
              disabled={submitting}>
              {opts.map(([v, l]: any) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
        ))}
      </div>

      <div className="queue-schedule-toggle">
        <label className="checkbox-label">
          <input type="checkbox" checked={scheduleEnabled}
            onChange={e => setSched(e.target.checked)} disabled={submitting} />
          <span>📅 Schedule YouTube publish times</span>
        </label>
      </div>

      {scheduleEnabled && (
        <div className="queue-schedule-fields">
          <div className="form-group">
            <label className="form-label">First publish (ISO 8601 with offset, e.g. 2026-09-10T09:00:00+05:00)</label>
            <input type="text" className="form-input"
              placeholder="2026-09-10T09:00:00+05:00"
              value={scheduleStart} onChange={e => setStart(e.target.value)}
              disabled={submitting} />
            <div className="form-hint form-hint--info">
              At least 15 min in the future. Videos upload as private then auto-publish.
            </div>
          </div>
          <div className="form-group">
            <label className="form-label">Interval between videos</label>
            <select className="form-select" value={interval}
              onChange={e => setInterval(Number(e.target.value))} disabled={submitting}>
              {[[30,'30 min'],[60,'1 hr'],[120,'2 hr'],[240,'4 hr'],[360,'6 hr'],[720,'12 hr'],[1440,'24 hr']].map(
                ([v,l]) => <option key={v} value={v}>{l}</option>
              )}
            </select>
          </div>
          {preview.length > 0 && (
            <div className="queue-schedule-preview">
              <div className="detail-label" style={{marginBottom:'0.3rem'}}>📅 Schedule preview</div>
              {preview.map((p,i) => <div key={i} className="queue-schedule-preview__item">{p}</div>)}
            </div>
          )}
        </div>
      )}

      {error && <div className="alert alert--error" style={{marginTop:'0.75rem'}}>{error}</div>}

      <div style={{marginTop:'1rem', display:'flex', gap:'0.75rem', alignItems:'center'}}>
        <button className="btn btn--primary" onClick={handleSubmit}
          disabled={submitting || parsedTopics.length === 0}>
          {submitting
            ? <><span className="tts-spinner" aria-hidden="true"/> Adding…</>
            : `📦 Add ${parsedTopics.length} topic${parsedTopics.length !== 1 ? 's' : ''} to queue`}
        </button>
        <span className="hint-text">One video at a time (i5-6300U CPU)</span>
      </div>
    </div>
  );
}

// ── QueueDashboard ────────────────────────────────────────────────────────────

function QueueDashboard({ onOpenLogs }: { onOpenLogs: (id: string) => void }) {
  const [jobs, setJobs]           = useState<QueueJob[]>([]);
  const [health, setHealth]       = useState<QueueHealth | null>(null);
  const [stats, setStats]         = useState<QueueStats | null>(null);
  const [currentJobId, setCurrent] = useState<string | null>(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState('');
  const [cleanup, setCleanup]     = useState<CleanupResult | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    load();
    pollingRef.current = setInterval(load, POLL_MS);
    return () => { if (pollingRef.current) clearInterval(pollingRef.current); };
  }, []);

  async function load() {
    try {
      const [j, h, s, st] = await Promise.all([
        listQueueJobs(), getQueueHealth(), getQueueStats(), getQueueStatus(),
      ]);
      setJobs(j); setHealth(h); setStats(s);
      setCurrent(st.current_job_id);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load queue.');
    } finally { setLoading(false); }
  }

  async function act(fn: () => Promise<unknown>, confirm_msg?: string) {
    if (confirm_msg && !window.confirm(confirm_msg)) return;
    try { await fn(); await load(); }
    catch (err) { alert(err instanceof Error ? err.message : 'Operation failed.'); }
  }

  async function handleCleanup(dryRun: boolean) {
    try {
      const result = await runCleanup(dryRun);
      setCleanup(result);
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Cleanup failed.');
    }
  }

  if (loading) return <p className="hint-text">Loading queue…</p>;
  if (error)   return <div className="alert alert--error">{error}</div>;

  const hasFailed = jobs.some(j => j.status === 'failed');

  return (
    <div className="queue-dashboard">
      {/* ── Health banner ── */}
      {health && (health.disk_warning || health.uploads_remaining === 0) && (
        <div className="alert alert--error" style={{marginBottom:'0.75rem'}}>
          {health.disk_warning && (
            <div>⚠ Low disk: {health.free_disk_gb.toFixed(1)} GB free</div>
          )}
          {health.uploads_remaining === 0 && (
            <div>⚠ Daily upload limit reached ({health.uploads_today}/{health.upload_limit})</div>
          )}
        </div>
      )}

      {/* ── Stats grid ── */}
      {stats && health && (
        <div className="queue-stats">
          <div className="queue-stats__grid">
            {[
              ['Total', stats.total, 'var(--color-text)'],
              ['Queued', stats.queued, 'var(--color-info)'],
              ['Processing', stats.processing, 'var(--color-warning)'],
              ['Completed', stats.completed, 'var(--color-success)'],
              ['Failed', stats.failed, 'var(--color-error)'],
              ['Cancelled', stats.cancelled, 'var(--color-muted)'],
            ].map(([label, val, color]) => (
              <div className="queue-stat" key={label as string}>
                <span className="queue-stat__num" style={{color: color as string}}>{val as number}</span>
                <span>{label as string}</span>
              </div>
            ))}
          </div>

          <div className="queue-stats__right">
            <div className="queue-stats__disk">
              <span className={health.disk_warning ? 'color-error' : 'hint-text'}>
                💾 {health.free_disk_gb.toFixed(1)} GB free
              </span>
            </div>
            <div className="hint-text" style={{fontSize:'0.8rem'}}>
              ⬆ {health.uploads_today}/{health.upload_limit} uploads today
            </div>
            {stats.avg_processing_minutes > 0 && (
              <div className="hint-text" style={{fontSize:'0.78rem'}}>
                ⏱ avg {stats.avg_processing_minutes.toFixed(1)} min/video
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Worker controls ── */}
      {health && (
        <div className="queue-controls" style={{marginBottom:'0.75rem'}}>
          {!health.worker_alive && (
            <button className="btn btn--primary btn--sm"
              onClick={() => act(() => startQueue())}>▶ Start</button>
          )}
          {health.worker_alive && !health.worker_paused && (
            <button className="btn btn--ghost btn--sm"
              onClick={() => act(() => pauseQueue())}>⏸ Pause</button>
          )}
          {health.worker_paused && (
            <button className="btn btn--primary btn--sm"
              onClick={() => act(() => resumeQueue())}>▶ Resume</button>
          )}
          {health.worker_alive && (
            <button className="btn btn--ghost btn--sm"
              onClick={() => act(() => stopQueue(), 'Stop the queue worker after the current job finishes?')}>
              ⏹ Stop
            </button>
          )}
          {hasFailed && (
            <button className="btn btn--ghost btn--sm"
              onClick={() => act(() => retryAllFailed())}>🔄 Retry All Failed</button>
          )}
          <button className="btn btn--ghost btn--sm"
            onClick={() => act(() => clearCompleted(), 'Delete all completed job records (files kept)?')}>
            🗑 Clear Completed
          </button>
          <button className="btn btn--ghost btn--sm"
            onClick={() => act(() => clearFailed(), 'Delete all failed/cancelled job records?')}>
            🗑 Clear Failed
          </button>
          <button className="btn btn--ghost btn--sm"
            onClick={() => handleCleanup(true)}>🧹 Cleanup (preview)</button>

          <span className={`queue-worker-badge ${health.worker_alive ? 'queue-worker-badge--on' : 'queue-worker-badge--off'}`}>
            {health.worker_alive ? (health.worker_paused ? '⏸ Paused' : '● Running') : '○ Stopped'}
          </span>
        </div>
      )}

      {/* ── Cleanup result ── */}
      {cleanup && (
        <div className="alert alert--info" style={{marginBottom:'0.75rem', fontSize:'0.85rem'}}>
          <strong>🧹 Cleanup {cleanup.dry_run ? '(preview)' : 'result'}:</strong>{' '}
          {cleanup.files_deleted} file{cleanup.files_deleted !== 1 ? 's' : ''} deleted ·{' '}
          {fmtBytes(cleanup.bytes_freed)} freed ·{' '}
          {cleanup.files_skipped} skipped
          {cleanup.errors.length > 0 && <div style={{color:'var(--color-error)'}}>Errors: {cleanup.errors.join(', ')}</div>}
          {cleanup.dry_run && (
            <button className="btn btn--ghost btn--sm" style={{marginLeft:'0.5rem'}}
              onClick={() => { setCleanup(null); handleCleanup(false); }}>
              Run Cleanup Now
            </button>
          )}
          <button className="btn btn--ghost btn--sm" style={{marginLeft:'0.5rem'}}
            onClick={() => setCleanup(null)}>✕</button>
        </div>
      )}

      {/* ── Job list ── */}
      {jobs.length === 0 ? (
        <div className="alert alert--info">No queue jobs yet. Add topics above.</div>
      ) : (
        <div className="queue-list">
          {jobs.map(job => (
            <QueueJobCard
              key={job.id}
              job={job}
              isCurrent={currentJobId === job.id}
              onRefresh={load}
              onOpenLogs={onOpenLogs}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── QueueJobCard ──────────────────────────────────────────────────────────────

function QueueJobCard({
  job, isCurrent, onRefresh, onOpenLogs,
}: {
  job: QueueJob;
  isCurrent: boolean;
  onRefresh: () => void;
  onOpenLogs: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (isActive(job.status)) {
      // Use persisted started_at timestamp if available
      const startTime = job.started_at ? new Date(job.started_at).getTime() : Date.now();
      const t = setInterval(() => setElapsed(Date.now() - startTime), 1000);
      return () => clearInterval(t);
    } else if (job.completed_at && job.started_at) {
      // For completed jobs, use the actual duration
      const completedTime = new Date(job.completed_at).getTime();
      const startTime = new Date(job.started_at).getTime();
      setElapsed(completedTime - startTime);
    } else {
      setElapsed(0);
    }
  }, [job.status, job.started_at, job.completed_at]);

  async function act(fn: () => Promise<unknown>, confirm_msg?: string) {
    if (confirm_msg && !window.confirm(confirm_msg)) return;
    setBusy(true);
    try { await fn(); onRefresh(); }
    catch (err) { alert(err instanceof Error ? err.message : 'Error'); }
    finally { setBusy(false); }
  }

  const active = isActive(job.status);
  const color  = STATUS_COLOR[job.status] ?? 'var(--color-muted)';

  // Stage progress steps
  const STAGES = [
    {key:'researching',       label:'Research'},
    {key:'generating_script', label:'Script'},
    {key:'generating_audio',  label:'Audio'},
    {key:'generating_video',  label:'Video'},
    {key:'uploading',         label:'Upload'},
  ];
  const currentStageIdx = STAGES.findIndex(s => s.key === job.status);

  return (
    <div className={`queue-job-card ${isCurrent ? 'queue-job-card--current' : ''}`}>
      <div className="queue-job-card__header">
        <div className="queue-job-card__topic" title={job.topic}>
          {job.topic.length > 65 ? job.topic.substring(0,62)+'…' : job.topic}
        </div>
        <span className="queue-job-card__status" style={{color}}>
          {STATUS_LABEL[job.status] ?? job.status}
        </span>
      </div>

      {/* Stage pipeline for active jobs */}
      {active && (
        <div className="queue-job-stages">
          {STAGES.map((s, i) => {
            const done    = i < currentStageIdx;
            const current = i === currentStageIdx;
            return (
              <div key={s.key}
                className={`queue-stage ${done?'queue-stage--done':''} ${current?'queue-stage--active':''}`}>
                <span className="queue-stage__dot">{done?'✓':current?'⟳':'○'}</span>
                <span>{s.label}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Progress bar */}
      {active && (
        <div style={{margin:'0.3rem 0'}}>
          <div className="video-progress-bar-outer" style={{height:'6px'}}>
            <div className="video-progress-bar-inner" style={{width:`${job.progress}%`}}/>
          </div>
          <div style={{display:'flex', justifyContent:'space-between', fontSize:'0.78rem', marginTop:'2px'}}>
            <span className="hint-text">{job.current_stage}</span>
            <span className="hint-text">{job.progress}%{elapsed > 0 ? ` · ${fmtNow(elapsed)}` : ''}</span>
          </div>
        </div>
      )}

      {/* Meta row */}
      <div className="queue-job-card__meta">
        <span>{fmtDur(job.target_duration_seconds)}</span>
        <span>{job.language.toUpperCase()}</span>
        <span>{job.tone}</span>
        {job.retry_count > 0 && (
          <span style={{color:'var(--color-warning)'}}>
            Retry {job.retry_count}/{job.max_retries}
          </span>
        )}
        {job.next_retry_at && job.status === 'queued' && (
          <span className="hint-text">
            ⏰ retry at {fmt(job.next_retry_at)}
          </span>
        )}
        {job.scheduled_publish_at && (
          <span style={{color:'#a78bfa'}}>📅 {fmt(job.scheduled_publish_at)}</span>
        )}
      </div>

      {/* YouTube link */}
      {job.youtube_url && (
        <div className="queue-job-card__yt">
          <a href={job.youtube_url} target="_blank" rel="noreferrer"
            className="btn btn--ghost btn--sm">▶ YouTube</a>
          {job.thumbnail_uploaded && <span className="hint-text">🖼 ✓</span>}
          {job.schedule_set       && <span className="hint-text">📅 ✓</span>}
        </div>
      )}

      {/* Error */}
      {job.status === 'failed' && job.error_message && (
        <div className="queue-job-error">{job.error_message.substring(0,220)}</div>
      )}

      {/* Timestamps */}
      <div className="hint-text" style={{fontSize:'0.73rem', marginTop:'0.2rem'}}>
        Created {fmt(job.created_at)}
        {job.completed_at && ` · Done ${fmt(job.completed_at)}`}
        {job.failed_at    && ` · Failed ${fmt(job.failed_at)}`}
      </div>

      {/* Actions */}
      <div className="queue-job-card__actions">
        <button className="btn btn--ghost btn--sm" disabled={busy}
          onClick={() => onOpenLogs(job.id)}>📋 Logs</button>
        {job.status === 'failed' && (
          <button className="btn btn--ghost btn--sm" disabled={busy}
            onClick={() => act(() => retryQueueJob(job.id))}>↺ Retry</button>
        )}
        {job.status === 'queued' && (
          <button className="btn btn--ghost btn--sm" disabled={busy}
            onClick={() => act(() => cancelQueueJob(job.id), 'Cancel this job?')}>
            🚫 Cancel
          </button>
        )}
        {['completed','cancelled','failed'].includes(job.status) && (
          <button className="btn btn--danger btn--sm" disabled={busy}
            onClick={() => act(() => deleteQueueJob(job.id), 'Delete this job record?')}>
            🗑 Delete
          </button>
        )}
      </div>
    </div>
  );
}

// ── JobLogsPanel ──────────────────────────────────────────────────────────────

function JobLogsPanel({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const [logs, setLogs]     = useState<JobLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getJobLogs(jobId)
      .then(setLogs)
      .catch(() => setError('Failed to load logs.'))
      .finally(() => setLoading(false));
  }, [jobId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const LEVEL_COLOR: Record<string, string> = {
    info:    'var(--color-muted)',
    warning: 'var(--color-warning)',
    error:   'var(--color-error)',
  };

  return (
    <div className="job-logs-panel">
      <div style={{display:'flex', alignItems:'center', gap:'0.75rem', marginBottom:'0.75rem'}}>
        <button className="btn btn--ghost btn--sm" onClick={onBack}>← Back</button>
        <h2 className="section-title" style={{margin:0}}>📋 Job Logs</h2>
      </div>

      {loading && <p className="hint-text">Loading logs…</p>}
      {error   && <div className="alert alert--error">{error}</div>}

      {!loading && !error && logs.length === 0 && (
        <div className="alert alert--info">No log entries for this job yet.</div>
      )}

      <div className="job-logs-list">
        {logs.map(entry => (
          <div key={entry.id} className="job-log-entry">
            <span className="job-log-entry__time">
              {new Date(entry.created_at).toLocaleTimeString()}
            </span>
            {entry.stage && (
              <span className="job-log-entry__stage">[{entry.stage}]</span>
            )}
            <span className="job-log-entry__msg"
              style={{color: LEVEL_COLOR[entry.level] ?? 'var(--color-text)'}}>
              {entry.message}
            </span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
