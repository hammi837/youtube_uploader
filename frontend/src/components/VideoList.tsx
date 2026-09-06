import { useEffect, useState } from 'react';
import { getVideos } from '../services/api';
import type { UploadJob } from '../types/api';
import { VideoCard } from './VideoCard';

export function VideoList() {
  const [jobs, setJobs]       = useState<UploadJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState('');

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const data = await getVideos();
      setJobs(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load videos.');
    } finally {
      setLoading(false);
    }
  }

  function handleUpdated(updated: UploadJob) {
    setJobs((prev) => prev.map((j) => (j.job_id === updated.job_id ? updated : j)));
  }

  if (loading) {
    return (
      <div className="video-list">
        <div className="video-list__loading">Loading history…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="video-list">
        <div className="alert alert--error">{error}</div>
        <button className="btn btn--ghost" onClick={load}>Retry</button>
      </div>
    );
  }

  return (
    <div className="video-list">
      <div className="video-list__header">
        <h2>Upload history</h2>
        <button className="btn btn--ghost btn--sm" onClick={load} title="Refresh">
          ↻ Refresh
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="video-list__empty">
          <div className="video-list__empty-icon">📭</div>
          <p>No videos yet. Upload one to get started.</p>
        </div>
      ) : (
        <div className="video-list__items">
          {jobs.map((job) => (
            <VideoCard key={job.job_id} job={job} onUpdated={handleUpdated} />
          ))}
        </div>
      )}
    </div>
  );
}
