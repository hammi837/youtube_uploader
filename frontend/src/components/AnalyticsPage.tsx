import { useEffect, useState } from 'react';
import {
  getAnalyticsErrors,
  getAnalyticsJobs,
  getAnalyticsPerformance,
  getAnalyticsResources,
  getAnalyticsSummary,
} from '../services/api';
import type {
  AnalyticsJobsResponse,
  AnalyticsSummary,
  ErrorAggregation,
  PerformanceMetrics,
  ResourceUsage,
} from '../types/api';

type DateRange = '7' | '30' | 'all';

function fmtBytes(gb: number): string {
  if (gb >= 1024) return `${(gb / 1024).toFixed(1)} TB`;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${(gb * 1024).toFixed(0)} MB`;
}

function fmtDur(s: number | null): string {
  if (s === null) return '—';
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

function fmtPct(pct: number): string {
  return `${(pct * 100).toFixed(1)}%`;
}

function exportToCSV(data: any[], filename: string) {
  if (data.length === 0) return;
  const headers = Object.keys(data[0]);
  const csv = [
    headers.join(','),
    ...data.map((row) =>
      headers
        .map((header) => {
          const val = row[header];
          if (val === null || val === undefined) return '';
          if (typeof val === 'string') return `"${val.replace(/"/g, '""')}"`;
          return String(val);
        })
        .join(',')
    ),
  ].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function AnalyticsPage() {
  const [dateRange, setDateRange] = useState<DateRange>('7');
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [performance, setPerformance] = useState<PerformanceMetrics | null>(null);
  const [errors, setErrors] = useState<ErrorAggregation | null>(null);
  const [resources, setResources] = useState<ResourceUsage | null>(null);
  const [jobs, setJobs] = useState<AnalyticsJobsResponse | null>(null);
  const [jobsPage, setJobsPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    loadAnalytics();
  }, [dateRange, jobsPage]);

  async function loadAnalytics() {
    setLoading(true);
    setError('');
    try {
      const [summaryData, perfData, errorsData, resourcesData] = await Promise.all([
        getAnalyticsSummary(dateRange),
        getAnalyticsPerformance(dateRange),
        getAnalyticsErrors(dateRange),
        getAnalyticsResources(),
      ]);
      setSummary(summaryData);
      setPerformance(perfData);
      setErrors(errorsData);
      setResources(resourcesData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load analytics');
    } finally {
      setLoading(false);
    }
  }

  async function loadJobs() {
    try {
      const jobsData = await getAnalyticsJobs(dateRange, undefined, undefined, jobsPage);
      setJobs(jobsData);
    } catch (err) {
      console.error('Failed to load jobs:', err);
    }
  }

  useEffect(() => {
    loadJobs();
  }, [dateRange, jobsPage]);

  if (loading) {
    return (
      <div className="analytics-page">
        <h2 className="section-title">📊 Analytics</h2>
        <p className="hint-text">Loading analytics...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="analytics-page">
        <h2 className="section-title">📊 Analytics</h2>
        <div className="alert alert--error">
          {error.includes('503') || error.includes('500')
            ? 'Analytics temporarily unavailable'
            : error}
        </div>
      </div>
    );
  }

  if (!summary || !performance || !errors || !resources) {
    return (
      <div className="analytics-page">
        <h2 className="section-title">📊 Analytics</h2>
        <p className="hint-text">No analytics data available.</p>
      </div>
    );
  }

  return (
    <div className="analytics-page">
      <h2 className="section-title">📊 Analytics</h2>

      {/* Date range selector */}
      <div className="analytics-controls">
        <label className="form-label">Date Range:</label>
        <select
          className="form-select"
          value={dateRange}
          onChange={(e) => setDateRange(e.target.value as DateRange)}
        >
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="all">All available records</option>
        </select>
        <button className="btn btn--secondary" onClick={loadAnalytics}>
          Refresh
        </button>
      </div>

      {/* Data availability note */}
      <div className="alert alert--info" style={{ fontSize: '0.85rem', marginTop: '1rem' }}>
        <strong>Data Availability:</strong> {summary.data_availability_note}
      </div>

      {/* Summary Cards */}
      <div className="analytics-summary">
        <div className="card">
          <div className="card__label">Total Jobs</div>
          <div className="card__value">{summary.total_jobs}</div>
        </div>
        <div className="card">
          <div className="card__label">Completed</div>
          <div className="card__value card__value--success">{summary.completed_jobs}</div>
        </div>
        <div className="card">
          <div className="card__label">Failed</div>
          <div className="card__value card__value--error">{summary.failed_jobs}</div>
        </div>
        <div className="card">
          <div className="card__label">Success Rate</div>
          <div className="card__value">{fmtPct(summary.success_rate)}</div>
        </div>
        <div className="card">
          <div className="card__label">Avg Duration</div>
          <div className="card__value">{fmtDur(summary.avg_total_duration_seconds)}</div>
        </div>
        <div className="card">
          <div className="card__label">Uploads Today</div>
          <div className="card__value">{summary.uploads_today}</div>
        </div>
        <div className="card">
          <div className="card__label">Disk Usage</div>
          <div className="card__value">{fmtBytes(summary.current_disk_usage_gb)}</div>
        </div>
      </div>

      {/* Performance Metrics */}
      <div className="analytics-section">
        <h3 className="section-subtitle">Performance Metrics</h3>
        <div className="alert alert--info" style={{ fontSize: '0.85rem' }}>
          {performance.data_availability_note}
        </div>
        <div className="analytics-grid">
          <div className="card">
            <div className="card__label">Jobs Completed</div>
            <div className="card__value">{performance.jobs_completed}</div>
          </div>
          <div className="card">
            <div className="card__label">Jobs with Retries</div>
            <div className="card__value">{performance.jobs_with_retries}</div>
          </div>
          <div className="card">
            <div className="card__label">Retry Rate</div>
            <div className="card__value">{fmtPct(performance.retry_rate)}</div>
          </div>
          <div className="card">
            <div className="card__label">P50 Duration</div>
            <div className="card__value">{fmtDur(performance.p50_duration_seconds)}</div>
          </div>
          <div className="card">
            <div className="card__label">P90 Duration</div>
            <div className="card__value">{fmtDur(performance.p90_duration_seconds)}</div>
          </div>
          <div className="card">
            <div className="card__label">P95 Duration</div>
            <div className="card__value">{fmtDur(performance.p95_duration_seconds)}</div>
          </div>
        </div>
        <button
          className="btn btn--secondary"
          onClick={() => exportToCSV([performance], `performance_${dateRange}d.csv`)}
        >
          Export CSV
        </button>
      </div>

      {/* Error Aggregation */}
      <div className="analytics-section">
        <h3 className="section-subtitle">Error Analysis</h3>
        <div className="alert alert--info" style={{ fontSize: '0.85rem' }}>
          {errors.data_availability_note}
        </div>
        <div className="analytics-grid">
          <div className="card">
            <div className="card__label">Total Errors</div>
            <div className="card__value card__value--error">{errors.total_errors}</div>
          </div>
          <div className="card">
            <div className="card__label">Transient</div>
            <div className="card__value">{errors.transient_errors}</div>
          </div>
          <div className="card">
            <div className="card__label">Permanent</div>
            <div className="card__value">{errors.permanent_errors}</div>
          </div>
          <div className="card">
            <div className="card__label">Unknown</div>
            <div className="card__value">{errors.unknown_errors}</div>
          </div>
        </div>

        {/* Error by type */}
        <div className="analytics-subsection">
          <h4>Errors by Type</h4>
          <table className="table">
            <thead>
              <tr>
                <th>Error Type</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {errors.error_by_type.map((item) => (
                <tr key={item.type}>
                  <td>{item.type}</td>
                  <td>{item.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Error by stage */}
        <div className="analytics-subsection">
          <h4>Errors by Stage</h4>
          <table className="table">
            <thead>
              <tr>
                <th>Stage</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {errors.error_by_stage.map((item) => (
                <tr key={item.stage}>
                  <td>{item.stage}</td>
                  <td>{item.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Top error messages */}
        {errors.top_error_messages.length > 0 && (
          <div className="analytics-subsection">
            <h4>Top Error Messages</h4>
            <ul className="list">
              {errors.top_error_messages.map((msg, i) => (
                <li key={i}>{msg}</li>
              ))}
            </ul>
          </div>
        )}

        <button
          className="btn btn--secondary"
          onClick={() => exportToCSV([errors], `errors_${dateRange}d.csv`)}
        >
          Export CSV
        </button>
      </div>

      {/* Resource Usage */}
      <div className="analytics-section">
        <h3 className="section-subtitle">Current Resource Usage</h3>
        <div className="alert alert--info" style={{ fontSize: '0.85rem' }}>
          {resources.note}
        </div>
        <div className="analytics-grid">
          <div className="card">
            <div className="card__label">Total Disk</div>
            <div className="card__value">{fmtBytes(resources.disk_usage_gb)}</div>
          </div>
          <div className="card">
            <div className="card__label">Free Disk</div>
            <div className="card__value">{fmtBytes(resources.free_disk_gb)}</div>
          </div>
          <div className="card">
            <div className="card__label">Temp Files</div>
            <div className="card__value">{fmtBytes(resources.temp_size_gb)}</div>
          </div>
          <div className="card">
            <div className="card__label">Audio Storage</div>
            <div className="card__value">{fmtBytes(resources.audio_size_gb)}</div>
          </div>
          <div className="card">
            <div className="card__label">Video Storage</div>
            <div className="card__value">{fmtBytes(resources.video_size_gb)}</div>
          </div>
          <div className="card">
            <div className="card__label">AI Images</div>
            <div className="card__value">{fmtBytes(resources.ai_images_size_gb)}</div>
          </div>
        </div>
        <button
          className="btn btn--secondary"
          onClick={() => exportToCSV([resources], `resources.csv`)}
        >
          Export CSV
        </button>
      </div>

      {/* Jobs List */}
      <div className="analytics-section">
        <h3 className="section-subtitle">Jobs</h3>
        {jobs && (
          <>
            <div className="analytics-subsection">
              <p>Total: {jobs.total_count} jobs</p>
              <div className="pagination">
                <button
                  className="btn btn--secondary"
                  disabled={jobs.page === 1}
                  onClick={() => setJobsPage(jobs.page - 1)}
                >
                  Previous
                </button>
                <span>Page {jobs.page}</span>
                <button
                  className="btn btn--secondary"
                  disabled={jobs.page * jobs.page_size >= jobs.total_count}
                  onClick={() => setJobsPage(jobs.page + 1)}
                >
                  Next
                </button>
              </div>
            </div>
            <table className="table">
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Status</th>
                  <th>Language</th>
                  <th>Retries</th>
                  <th>Started</th>
                  <th>Completed</th>
                </tr>
              </thead>
              <tbody>
                {jobs.jobs.map((job) => (
                  <tr key={job.id}>
                    <td>{job.topic}</td>
                    <td>{job.status}</td>
                    <td>{job.language}</td>
                    <td>{job.retry_count}</td>
                    <td>{job.started_at ? new Date(job.started_at).toLocaleString() : '—'}</td>
                    <td>{job.completed_at ? new Date(job.completed_at).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button
              className="btn btn--secondary"
              onClick={() => exportToCSV(jobs.jobs, `jobs_${dateRange}d_page${jobs.page}.csv`)}
            >
              Export CSV
            </button>
          </>
        )}
      </div>
    </div>
  );
}
