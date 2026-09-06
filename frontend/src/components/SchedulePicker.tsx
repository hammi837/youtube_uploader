import { useEffect, useState } from 'react';

interface SchedulePickerProps {
  value: string;          // ISO local datetime string: "2026-09-05T18:00"
  onChange: (value: string) => void;
  error?: string;
}

export function SchedulePicker({ value, onChange, error }: SchedulePickerProps) {
  const [tz, setTz] = useState('');

  useEffect(() => {
    // Show the user's browser timezone name
    try {
      setTz(Intl.DateTimeFormat().resolvedOptions().timeZone);
    } catch {
      setTz('unknown');
    }
  }, []);

  // Minimum datetime = now + 5 minutes (browser-side guard)
  const minDateTime = new Date(Date.now() + 5 * 60 * 1000)
    .toISOString()
    .slice(0, 16);

  return (
    <div className="schedule-picker">
      <label className="form-label" htmlFor="schedule-datetime">
        Publish date &amp; time
      </label>

      <div className="schedule-picker__tz-note">
        Your timezone: <strong>{tz}</strong>
      </div>

      <input
        id="schedule-datetime"
        type="datetime-local"
        className={`form-input ${error ? 'form-input--error' : ''}`}
        value={value}
        min={minDateTime}
        onChange={(e) => onChange(e.target.value)}
      />

      {error && <div className="form-error">{error}</div>}

      <p className="schedule-picker__note">
        Scheduled videos are uploaded as <strong>private</strong> and automatically
        become <strong>public</strong> at the scheduled time.
      </p>
    </div>
  );
}
