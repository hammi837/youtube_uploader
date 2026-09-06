import { useRef, useState } from 'react';
import { createUpload } from '../services/api';
import type { UploadJob } from '../types/api';
import { SchedulePicker } from './SchedulePicker';

interface UploadFormProps {
  onJobStarted: (job: UploadJob) => void;
}

const ACCEPTED_VIDEO = '.mp4,.mov,.avi,.mkv,.webm,.wmv,.flv,.m4v';
const ACCEPTED_IMAGE  = '.jpg,.jpeg,.png';

const CATEGORIES = [
  { id: '22', label: 'People & Blogs' },
  { id: '28', label: 'Science & Technology' },
  { id: '26', label: 'How-to & Style' },
  { id: '24', label: 'Entertainment' },
  { id: '20', label: 'Gaming' },
  { id: '10', label: 'Music' },
  { id: '17', label: 'Sports' },
  { id: '25', label: 'News & Politics' },
  { id: '1',  label: 'Film & Animation' },
  { id: '2',  label: 'Autos & Vehicles' },
];

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function UploadForm({ onJobStarted }: UploadFormProps) {
  // File
  const [videoFile, setVideoFile]         = useState<File | null>(null);
  const [thumbFile, setThumbFile]         = useState<File | null>(null);
  const videoRef = useRef<HTMLInputElement>(null);
  const thumbRef = useRef<HTMLInputElement>(null);

  // Metadata
  const [title, setTitle]                 = useState('');
  const [description, setDescription]     = useState('');
  const [tagInput, setTagInput]           = useState('');
  const [tags, setTags]                   = useState<string[]>([]);
  const [categoryId, setCategoryId]       = useState('22');
  const [privacy, setPrivacy]             = useState<'private' | 'unlisted' | 'public'>('private');

  // Scheduling
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [scheduleValue, setScheduleValue]     = useState('');

  // UI state
  const [submitting, setSubmitting]       = useState(false);
  const [errors, setErrors]               = useState<Record<string, string>>({});
  const [submitError, setSubmitError]     = useState('');

  // ── Tag handling ──────────────────────────────────────────────────────────

  function addTag(raw: string) {
    const trimmed = raw.trim().replace(/,$/, '');
    if (trimmed && !tags.includes(trimmed)) {
      setTags([...tags, trimmed]);
    }
    setTagInput('');
  }

  function handleTagKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addTag(tagInput);
    } else if (e.key === 'Backspace' && tagInput === '' && tags.length > 0) {
      setTags(tags.slice(0, -1));
    }
  }

  function removeTag(tag: string) {
    setTags(tags.filter((t) => t !== tag));
  }

  // ── Validation ────────────────────────────────────────────────────────────

  function validate(): boolean {
    const errs: Record<string, string> = {};

    if (!videoFile) errs.video = 'Please select a video file.';
    if (!title.trim()) errs.title = 'Title is required.';
    if (title.length > 100) errs.title = 'Title must be 100 characters or less.';
    if (description.length > 5000) errs.description = 'Description must be 5000 characters or less.';

    if (scheduleEnabled) {
      if (!scheduleValue) {
        errs.schedule = 'Please select a publish date and time.';
      } else {
        const dt = new Date(scheduleValue);
        if (isNaN(dt.getTime())) {
          errs.schedule = 'Invalid date/time.';
        } else if (dt.getTime() <= Date.now() + 2 * 60 * 1000) {
          errs.schedule = 'Scheduled time must be at least 2 minutes in the future.';
        }
      }
    }

    if (thumbFile) {
      const ext = thumbFile.name.split('.').pop()?.toLowerCase() ?? '';
      if (!['jpg', 'jpeg', 'png'].includes(ext)) {
        errs.thumbnail = 'Thumbnail must be a JPEG or PNG image.';
      }
      if (thumbFile.size > 2 * 1024 * 1024) {
        errs.thumbnail = 'Thumbnail must be under 2 MB.';
      }
    }

    setErrors(errs);
    return Object.keys(errs).length === 0;
  }

  // ── Submit ────────────────────────────────────────────────────────────────

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError('');

    if (!validate() || !videoFile) return;

    setSubmitting(true);

    // Build timezone-aware scheduled_at string with explicit offset
    let scheduledAt: string | undefined;
    let timezone: string | undefined;

    if (scheduleEnabled && scheduleValue) {
      // scheduleValue is "YYYY-MM-DDTHH:mm" (local, no offset)
      // We append the browser's UTC offset so the backend converts correctly
      const offsetMinutes = new Date().getTimezoneOffset(); // negative for ahead of UTC
      const absMin  = Math.abs(offsetMinutes);
      const sign    = offsetMinutes <= 0 ? '+' : '-';
      const hh      = String(Math.floor(absMin / 60)).padStart(2, '0');
      const mm      = String(absMin % 60).padStart(2, '0');
      scheduledAt   = `${scheduleValue}:00${sign}${hh}:${mm}`;
      timezone      = Intl.DateTimeFormat().resolvedOptions().timeZone;
    }

    try {
      const job = await createUpload({
        file:           videoFile,
        title:          title.trim(),
        description:    description.trim(),
        tags:           [...tags, ...(tagInput.trim() ? [tagInput.trim()] : [])].join(','),
        category_id:    categoryId,
        privacy_status: scheduleEnabled ? 'private' : privacy,
        scheduled_at:   scheduledAt,
        timezone,
      });

      // Store thumbnail file reference on the job so the progress screen can upload it
      if (thumbFile) {
        (job as UploadJob & { _thumbFile?: File })._thumbFile = thumbFile;
      }

      onJobStarted(job);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Upload failed. Please try again.');
      setSubmitting(false);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <form className="upload-form" onSubmit={handleSubmit} noValidate>
      <h2 className="upload-form__title">Upload Video</h2>

      {/* ── Video file ── */}
      <div className="form-group">
        <label className="form-label" htmlFor="video-file">
          Video file <span className="required">*</span>
        </label>
        <div
          className={`file-drop ${videoFile ? 'file-drop--has-file' : ''} ${errors.video ? 'file-drop--error' : ''}`}
          onClick={() => videoRef.current?.click()}
          onKeyDown={(e) => e.key === 'Enter' && videoRef.current?.click()}
          role="button"
          tabIndex={0}
          aria-label="Select video file"
        >
          {videoFile ? (
            <div className="file-drop__info">
              <span className="file-drop__name">📹 {videoFile.name}</span>
              <span className="file-drop__size">{formatBytes(videoFile.size)}</span>
              <button
                type="button"
                className="file-drop__clear"
                onClick={(e) => { e.stopPropagation(); setVideoFile(null); if (videoRef.current) videoRef.current.value = ''; }}
                aria-label="Remove file"
              >✕</button>
            </div>
          ) : (
            <div className="file-drop__prompt">
              <span className="file-drop__icon">📁</span>
              <span>Click to select a video file</span>
              <span className="file-drop__formats">MP4, MOV, AVI, MKV, WebM and more</span>
            </div>
          )}
        </div>
        <input
          ref={videoRef}
          id="video-file"
          type="file"
          accept={ACCEPTED_VIDEO}
          className="visually-hidden"
          onChange={(e) => { setVideoFile(e.target.files?.[0] ?? null); setErrors((p) => ({ ...p, video: '' })); }}
        />
        {errors.video && <div className="form-error">{errors.video}</div>}
      </div>

      {/* ── Title ── */}
      <div className="form-group">
        <label className="form-label" htmlFor="title">
          Title <span className="required">*</span>
        </label>
        <input
          id="title"
          type="text"
          className={`form-input ${errors.title ? 'form-input--error' : ''}`}
          value={title}
          maxLength={100}
          placeholder="Enter video title"
          onChange={(e) => { setTitle(e.target.value); setErrors((p) => ({ ...p, title: '' })); }}
        />
        <div className="form-hint">{title.length}/100</div>
        {errors.title && <div className="form-error">{errors.title}</div>}
      </div>

      {/* ── Description ── */}
      <div className="form-group">
        <label className="form-label" htmlFor="description">Description</label>
        <textarea
          id="description"
          className={`form-textarea ${errors.description ? 'form-input--error' : ''}`}
          value={description}
          maxLength={5000}
          rows={4}
          placeholder="Optional video description"
          onChange={(e) => { setDescription(e.target.value); setErrors((p) => ({ ...p, description: '' })); }}
        />
        <div className="form-hint">{description.length}/5000</div>
        {errors.description && <div className="form-error">{errors.description}</div>}
      </div>

      {/* ── Tags ── */}
      <div className="form-group">
        <label className="form-label" htmlFor="tags">Tags</label>
        <div className="tags-input">
          {tags.map((tag) => (
            <span key={tag} className="tag">
              {tag}
              <button
                type="button"
                className="tag__remove"
                onClick={() => removeTag(tag)}
                aria-label={`Remove tag ${tag}`}
              >✕</button>
            </span>
          ))}
          <input
            id="tags"
            type="text"
            className="tags-input__field"
            value={tagInput}
            placeholder={tags.length === 0 ? 'Type a tag and press Enter or comma' : 'Add more…'}
            onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={handleTagKeyDown}
            onBlur={() => { if (tagInput.trim()) addTag(tagInput); }}
          />
        </div>
        <div className="form-hint">Press Enter or comma to add a tag</div>
      </div>

      {/* ── Category ── */}
      <div className="form-group">
        <label className="form-label" htmlFor="category">Category</label>
        <select
          id="category"
          className="form-select"
          value={categoryId}
          onChange={(e) => setCategoryId(e.target.value)}
        >
          {CATEGORIES.map((c) => (
            <option key={c.id} value={c.id}>{c.label}</option>
          ))}
        </select>
      </div>

      {/* ── Privacy ── */}
      <div className="form-group">
        <label className="form-label">Privacy</label>
        <div className="radio-group">
          {(['private', 'unlisted', 'public'] as const).map((p) => (
            <label
              key={p}
              className={`radio-option ${scheduleEnabled && p !== 'private' ? 'radio-option--disabled' : ''}`}
            >
              <input
                type="radio"
                name="privacy"
                value={p}
                checked={scheduleEnabled ? p === 'private' : privacy === p}
                disabled={scheduleEnabled && p !== 'private'}
                onChange={() => !scheduleEnabled && setPrivacy(p)}
              />
              <span className="radio-option__label">
                {p === 'private' && '🔒 Private'}
                {p === 'unlisted' && '🔗 Unlisted'}
                {p === 'public' && '🌐 Public'}
              </span>
            </label>
          ))}
        </div>
        {scheduleEnabled && (
          <div className="form-hint form-hint--info">
            Privacy is set to Private automatically for scheduled videos.
          </div>
        )}
      </div>

      {/* ── Schedule toggle ── */}
      <div className="form-group">
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={scheduleEnabled}
            onChange={(e) => {
              setScheduleEnabled(e.target.checked);
              if (!e.target.checked) setScheduleValue('');
              setErrors((p) => ({ ...p, schedule: '' }));
            }}
          />
          <span>Schedule publish time</span>
        </label>
      </div>

      {scheduleEnabled && (
        <SchedulePicker
          value={scheduleValue}
          onChange={(v) => { setScheduleValue(v); setErrors((p) => ({ ...p, schedule: '' })); }}
          error={errors.schedule}
        />
      )}

      {/* ── Thumbnail ── */}
      <div className="form-group">
        <label className="form-label" htmlFor="thumbnail">Thumbnail (optional)</label>
        <div
          className={`file-drop file-drop--small ${thumbFile ? 'file-drop--has-file' : ''} ${errors.thumbnail ? 'file-drop--error' : ''}`}
          onClick={() => thumbRef.current?.click()}
          onKeyDown={(e) => e.key === 'Enter' && thumbRef.current?.click()}
          role="button"
          tabIndex={0}
          aria-label="Select thumbnail image"
        >
          {thumbFile ? (
            <div className="file-drop__info">
              <span className="file-drop__name">🖼 {thumbFile.name}</span>
              <span className="file-drop__size">{formatBytes(thumbFile.size)}</span>
              <button
                type="button"
                className="file-drop__clear"
                onClick={(e) => { e.stopPropagation(); setThumbFile(null); if (thumbRef.current) thumbRef.current.value = ''; }}
                aria-label="Remove thumbnail"
              >✕</button>
            </div>
          ) : (
            <div className="file-drop__prompt">
              <span>Click to select thumbnail</span>
              <span className="file-drop__formats">JPEG or PNG, max 2 MB, 1280×720 recommended</span>
            </div>
          )}
        </div>
        <input
          ref={thumbRef}
          id="thumbnail"
          type="file"
          accept={ACCEPTED_IMAGE}
          className="visually-hidden"
          onChange={(e) => { setThumbFile(e.target.files?.[0] ?? null); setErrors((p) => ({ ...p, thumbnail: '' })); }}
        />
        {errors.thumbnail && <div className="form-error">{errors.thumbnail}</div>}
        <div className="form-hint">
          Requires a verified YouTube channel. Uploaded after video processing completes.
        </div>
      </div>

      {/* ── Submit error ── */}
      {submitError && (
        <div className="alert alert--error" role="alert">
          <strong>Upload failed:</strong> {submitError}
        </div>
      )}

      {/* ── Submit ── */}
      <button
        type="submit"
        className="btn btn--primary btn--full"
        disabled={submitting}
      >
        {submitting
          ? 'Starting upload…'
          : scheduleEnabled
            ? '📅 Schedule Video'
            : '⬆ Upload Video'}
      </button>
    </form>
  );
}
