import { useEffect, useRef, useState } from 'react';
import { getUploadStatus, uploadThumbnail } from '../services/api';
import type { UploadJob } from '../types/api';
import { ProgressBar } from './ProgressBar';

interface UploadProgressProps {
  job: UploadJob & { _thumbFile?: File };
  onComplete: (job: UploadJob) => void;
  onRetry: () => void;
}

const POLL_INTERVAL_MS = 1500;
const TERMINAL_STATUSES = new Set(['published', 'scheduled', 'failed', 'cancelled']);

export function UploadProgress({ job: initialJob, onComplete, onRetry }: UploadProgressProps) {
  const [job, setJob] = useState<UploadJob>(initialJob);
  const [thumbStatus, setThumbStatus] = useState<'idle' | 'uploading' | 'done' | 'error'>('idle');
  const [thumbError, setThumbError] = useState('');
  const thumbFile = (initialJob as UploadJob & { _thumbFile?: File })._thumbFile;
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const thumbUploadedRef = useRef(false);

  useEffect(() => {
    pollingRef.current = setInterval(async () => {
      try {
        const updated = await getUploadStatus(initialJob.job_id);
        setJob(updated);

        if (TERMINAL_STATUSES.has(updated.status)) {
          // Stop polling immediately
          if (pollingRef.current) {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
          }

          if (updated.status !== 'failed') {
            // Upload thumbnail first (if provided), THEN notify parent.
            // This applies to both immediate uploads (published) and scheduled
            // uploads (scheduled) — both have a valid video_id at this point.
            // We do NOT wait for the video to become public; the thumbnail API
            // accepts uploads on private/scheduled videos immediately.
            if (
              updated.video_id &&
              thumbFile &&
              !thumbUploadedRef.current
            ) {
              thumbUploadedRef.current = true;
              setThumbStatus('uploading');
              try {
                await uploadThumbnail(updated.video_id, thumbFile);
                setThumbStatus('done');
              } catch (e) {
                setThumbStatus('error');
                setThumbError(e instanceof Error ? e.message : 'Thumbnail upload failed.');
              }
            }

            onComplete(updated);
          }
        }
      } catch {
        // network glitch — keep polling
      }
    }, POLL_INTERVAL_MS);

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [initialJob.job_id, thumbFile, onComplete]);

  const isTerminal = TERMINAL_STATUSES.has(job.status);
  const isFailed   = job.status === 'failed';

  function formatScheduled(iso: string | null): string {
    if (!iso) return '';
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

  return (
    <div className="upload-progress">
      <h2 className="upload-progress__title">
        {isFailed
          ? '❌ Upload Failed'
          : isTerminal
            ? job.status === 'scheduled' ? '📅 Video Scheduled' : '✅ Upload Complete'
            : '⬆ Uploading…'}
      </h2>

      <div className="upload-progress__filename">{job.filename}</div>

      {!isTerminal && (
        <ProgressBar
          progress={job.progress}
          label={`Status: ${job.status}`}
          indeterminate={job.status === 'uploading' && job.progress === 0}
        />
      )}

      {isTerminal && !isFailed && (
        <div className="upload-progress__success">
          <div className="detail-row">
            <span className="detail-label">Video ID</span>
            <span className="detail-value">{job.video_id}</span>
          </div>
          {job.url && (
            <div className="detail-row">
              <span className="detail-label">YouTube URL</span>
              <a
                href={job.url}
                target="_blank"
                rel="noopener noreferrer"
                className="detail-link"
              >
                {job.url}
              </a>
            </div>
          )}
          {job.scheduled_at && (
            <div className="detail-row">
              <span className="detail-label">Publishes at</span>
              <span className="detail-value detail-value--highlight">
                {formatScheduled(job.scheduled_at)}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Thumbnail status — shown as soon as thumb upload starts */}
      {thumbFile && thumbStatus !== 'idle' && (
        <div className={`thumb-status thumb-status--${thumbStatus}`}>
          {thumbStatus === 'uploading' && '⏳ Uploading thumbnail…'}
          {thumbStatus === 'done'      && '✓ Thumbnail uploaded'}
          {thumbStatus === 'error'     && `⚠ Thumbnail failed: ${thumbError}`}
        </div>
      )}

      {isFailed && (
        <div className="upload-progress__error">
          <div className="alert alert--error">
            <strong>Error:</strong> {job.error_message ?? 'An unknown error occurred.'}
          </div>
          <button className="btn btn--secondary" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}

      {isTerminal && !isFailed && (
        <button className="btn btn--primary" onClick={onRetry}>
          Upload another video
        </button>
      )}
    </div>
  );
}
