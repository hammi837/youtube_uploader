interface ProgressBarProps {
  progress: number;         // 0–100
  label?: string;
  showPercent?: boolean;
  indeterminate?: boolean;  // show animated pulse when progress is unknown
}

export function ProgressBar({
  progress,
  label,
  showPercent = true,
  indeterminate = false,
}: ProgressBarProps) {
  const clamped = Math.min(100, Math.max(0, progress));

  return (
    <div className="progress-bar">
      {label && <div className="progress-bar__label">{label}</div>}
      <div className="progress-bar__track">
        <div
          className={`progress-bar__fill ${indeterminate ? 'progress-bar__fill--indeterminate' : ''}`}
          style={indeterminate ? undefined : { width: `${clamped}%` }}
          role="progressbar"
          aria-valuenow={indeterminate ? undefined : clamped}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={indeterminate ? 'Uploading…' : undefined}
        />
      </div>
      {showPercent && !indeterminate && (
        <div className="progress-bar__percent">{clamped}%</div>
      )}
      {indeterminate && (
        <div className="progress-bar__percent">Uploading…</div>
      )}
    </div>
  );
}
