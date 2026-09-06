import { useRef, useState } from 'react';
import { cancelSchedule, updateVideo, uploadThumbnail } from '../services/api';
import type { UploadJob } from '../types/api';
import { ProgressBar } from './ProgressBar';

interface VideoCardProps {
  job: UploadJob;
  onUpdated: (job: UploadJob) => void;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    // Convert the UTC timestamp to the user's local timezone for display
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

const STATUS_LABELS: Record<string, string> = {
  pending:   '⏳ Pending',
  uploading: '⬆ Uploading',
  scheduled: '📅 Scheduled',
  published: '✅ Published',
  failed:    '❌ Failed',
  cancelled: '🚫 Cancelled',
};

export function VideoCard({ job, onUpdated }: VideoCardProps) {
  const [expanded, setExpanded]     = useState(false);
  const [editing, setEditing]       = useState(false);
  const [title, setTitle]           = useState(job.title);
  const [description, setDesc]      = useState(job.description);
  const [tags, setTags]             = useState(job.tags.join(', '));
  const [saving, setSaving]         = useState(false);
  const [saveError, setSaveError]   = useState('');
  const [cancelling, setCancelling] = useState(false);
  const thumbInputRef               = useRef<HTMLInputElement>(null);
  const [thumbMsg, setThumbMsg]     = useState('');

  async function handleSave() {
    setSaving(true);
    setSaveError('');
    try {
      await updateVideo(job.video_id!, {
        title:       title.trim(),
        description: description.trim(),
        tags:        tags.split(',').map((t) => t.trim()).filter(Boolean),
      });
      onUpdated({ ...job, title: title.trim(), description: description.trim(), tags: tags.split(',').map((t) => t.trim()).filter(Boolean) });
      setEditing(false);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Save failed.');
    } finally {
      setSaving(false);
    }
  }

  async function handleCancelSchedule() {
    if (!window.confirm('Cancel the scheduled publish? The video will remain private permanently.')) return;
    setCancelling(true);
    try {
      await cancelSchedule(job.video_id!);
      onUpdated({ ...job, scheduled_at: null, status: 'cancelled' });
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Cancel failed.');
    } finally {
      setCancelling(false);
    }
  }

  async function handleThumbChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !job.video_id) return;
    setThumbMsg('Uploading thumbnail…');
    try {
      await uploadThumbnail(job.video_id, file);
      setThumbMsg('✓ Thumbnail updated');
    } catch (err) {
      setThumbMsg(`⚠ ${err instanceof Error ? err.message : 'Failed'}`);
    }
  }

  return (
    <div className={`video-card video-card--${job.status}`}>
      {/* Header */}
      <div className="video-card__header" onClick={() => setExpanded((v) => !v)}>
        <div className="video-card__title-row">
          <span className="video-card__title">{job.title}</span>
          <span className={`status-badge status-badge--${job.status}`}>
            {STATUS_LABELS[job.status] ?? job.status}
          </span>
        </div>
        <div className="video-card__meta">
          <span>{job.filename}</span>
          <span>Uploaded {formatDate(job.created_at)}</span>
        </div>
        {job.status === 'uploading' && (
          <ProgressBar progress={job.progress} showPercent />
        )}
        <span className="video-card__chevron">{expanded ? '▲' : '▼'}</span>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="video-card__body">
          {/* Details */}
          {job.video_id && (
            <div className="detail-row">
              <span className="detail-label">Video ID</span>
              <span className="detail-value">{job.video_id}</span>
            </div>
          )}
          {job.url && (
            <div className="detail-row">
              <span className="detail-label">YouTube URL</span>
              <a href={job.url} target="_blank" rel="noopener noreferrer" className="detail-link">
                {job.url}
              </a>
            </div>
          )}
          {job.scheduled_at && (
            <div className="detail-row">
              <span className="detail-label">Publishes at</span>
              <span className="detail-value detail-value--highlight">{formatDate(job.scheduled_at)}</span>
            </div>
          )}
          {job.error_message && (
            <div className="alert alert--error" style={{ marginTop: '0.75rem' }}>
              {job.error_message}
            </div>
          )}

          {/* Edit metadata */}
          {job.video_id && (
            <>
              {editing ? (
                <div className="edit-form">
                  <div className="form-group">
                    <label className="form-label">Title</label>
                    <input className="form-input" value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Description</label>
                    <textarea className="form-textarea" rows={3} value={description} onChange={(e) => setDesc(e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Tags (comma-separated)</label>
                    <input className="form-input" value={tags} onChange={(e) => setTags(e.target.value)} />
                  </div>
                  {saveError && <div className="form-error">{saveError}</div>}
                  <div className="edit-form__actions">
                    <button className="btn btn--primary btn--sm" onClick={handleSave} disabled={saving}>
                      {saving ? 'Saving…' : 'Save'}
                    </button>
                    <button className="btn btn--ghost btn--sm" onClick={() => setEditing(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button className="btn btn--ghost btn--sm" onClick={() => setEditing(true)}>
                  ✏ Edit metadata
                </button>
              )}

              {/* Thumbnail */}
              <div className="video-card__thumb-row">
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={() => thumbInputRef.current?.click()}
                >
                  🖼 {thumbMsg ? 'Change thumbnail' : 'Upload thumbnail'}
                </button>
                {thumbMsg && <span className="thumb-status">{thumbMsg}</span>}
                <input
                  ref={thumbInputRef}
                  type="file"
                  accept=".jpg,.jpeg,.png"
                  className="visually-hidden"
                  onChange={handleThumbChange}
                />
              </div>

              {/* Cancel schedule */}
              {job.status === 'scheduled' && job.scheduled_at && (
                <button
                  className="btn btn--danger btn--sm"
                  onClick={handleCancelSchedule}
                  disabled={cancelling}
                >
                  {cancelling ? 'Cancelling…' : '🚫 Cancel schedule'}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
