import { useEffect, useState } from 'react';
import { deleteContentProject, getContentProject, getContentProjects } from '../services/api';
import type { ContentProjectDetail, ContentProjectSummary } from '../types/api';
import { ScriptViewer } from './ScriptViewer';

const STATUS_LABELS: Record<string, string> = {
  pending:     '⏳ Pending',
  researching: '🔍 Researching',
  generating:  '✍ Generating',
  completed:   '✅ Completed',
  failed:      '❌ Failed',
};

function formatDate(iso: string): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: tz,
    });
  } catch {
    return iso;
  }
}

function formatSeconds(s: number): string {
  const m = Math.floor(s / 60);
  return m === 0 ? `${s}s` : `${m}m`;
}

export function ContentList() {
  const [projects, setProjects]   = useState<ContentProjectSummary[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState('');
  const [viewing, setViewing]     = useState<ContentProjectDetail | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);

  useEffect(() => { load(); }, []);

  async function load() {
    setLoading(true);
    setError('');
    try {
      setProjects(await getContentProjects());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load content.');
    } finally {
      setLoading(false);
    }
  }

  async function handleView(id: string) {
    setLoadingId(id);
    try {
      const detail = await getContentProject(id);
      setViewing(detail);
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to load project.');
    } finally {
      setLoadingId(null);
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm('Delete this content project?')) return;
    try {
      await deleteContentProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Delete failed.');
    }
  }

  function handleViewerDeleted(id: string) {
    setProjects((prev) => prev.filter((p) => p.id !== id));
    setViewing(null);
  }

  // ── Script viewer ──────────────────────────────────────────────────────────
  if (viewing) {
    return (
      <ScriptViewer
        project={viewing}
        onBack={() => setViewing(null)}
        onDeleted={handleViewerDeleted}
      />
    );
  }

  // ── Loading / error ────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="content-list">
        <div className="video-list__loading">Loading content history…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="content-list">
        <div className="alert alert--error">{error}</div>
        <button className="btn btn--ghost" onClick={load}>Retry</button>
      </div>
    );
  }

  // ── Empty state ────────────────────────────────────────────────────────────
  if (projects.length === 0) {
    return (
      <div className="content-list">
        <div className="video-list__header">
          <h2>Content History</h2>
          <button className="btn btn--ghost btn--sm" onClick={load}>↻ Refresh</button>
        </div>
        <div className="video-list__empty">
          <div className="video-list__empty-icon">📄</div>
          <p>No generated scripts yet. Use the AI Content tab to create one.</p>
        </div>
      </div>
    );
  }

  // ── List ───────────────────────────────────────────────────────────────────
  return (
    <div className="content-list">
      <div className="video-list__header">
        <h2>Content History</h2>
        <button className="btn btn--ghost btn--sm" onClick={load} title="Refresh">
          ↻ Refresh
        </button>
      </div>

      <div className="video-list__items">
        {projects.map((p) => (
          <div key={p.id} className={`content-card content-card--${p.status}`}>
            <div className="content-card__main">
              <div className="content-card__title-row">
                <span className="content-card__topic">{p.topic}</span>
                <span className={`status-badge status-badge--${p.status}`}>
                  {STATUS_LABELS[p.status] ?? p.status}
                </span>
              </div>
              <div className="content-card__meta">
                <span>{p.language.toUpperCase()}</span>
                <span style={{ textTransform: 'capitalize' }}>{p.tone}</span>
                <span>{formatSeconds(p.target_duration_seconds)} target</span>
                <span>{p.scene_count} scenes</span>
                <span>{formatDate(p.created_at)}</span>
              </div>
            </div>
            <div className="content-card__actions">
              {p.status === 'completed' && p.has_script && (
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={() => handleView(p.id)}
                  disabled={loadingId === p.id}
                >
                  {loadingId === p.id ? 'Loading…' : '📄 View Script'}
                </button>
              )}
              <button
                className="btn btn--danger btn--sm"
                onClick={() => handleDelete(p.id)}
              >
                🗑
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
