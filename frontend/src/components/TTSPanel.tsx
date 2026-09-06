import { useEffect, useRef, useState } from 'react';
import {
  deleteAudioRecord,
  generateAudioFromContent,
  getAudioRecord,
  getAudioStreamUrl,
  getTTSVoices,
} from '../services/api';
import type { AudioRecord, ContentProjectDetail, TTSVoice } from '../types/api';

interface TTSPanelProps {
  project: ContentProjectDetail;
}

type GenerationPhase =
  | 'idle'
  | 'trying-edge'
  | 'falling-back'
  | 'generating-local'
  | 'done'
  | 'error';

const POLL_MS = 2000;

// Mode labels shown in the selector
const PROVIDER_MODES = [
  {
    value: 'auto',
    label: 'Auto — Edge → Local fallback',
    hint: 'Edge-TTS is tried first. If it is temporarily unavailable, Local CPU voice is used automatically.',
  },
  {
    value: 'edge',
    label: 'Edge-TTS only',
    hint: 'Uses Microsoft Edge-TTS. Requires internet. High quality neural voices.',
  },
  {
    value: 'local',
    label: 'Local CPU voice',
    hint: 'Uses Windows SAPI voices (offline). No internet required. Lower quality but always available.',
  },
];

const PROVIDER_BADGE: Record<string, string> = {
  edge:  'Edge-TTS',
  local: 'Local CPU',
};

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) return '—';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function TTSPanel({ project }: TTSPanelProps) {
  const [voices, setVoices]               = useState<TTSVoice[]>([]);
  const [voicesLoading, setVoicesLoading] = useState(false);
  const [voicesError, setVoicesError]     = useState('');
  const [selectedVoice, setSelectedVoice] = useState('en-US-AriaNeural');
  const [providerMode, setProviderMode]   = useState('auto');

  const [phase, setPhase]   = useState<GenerationPhase>('idle');
  const [audio, setAudio]   = useState<AudioRecord | null>(null);
  const [error, setError]   = useState('');
  const [deleting, setDeleting] = useState(false);

  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    loadVoices();
    return () => stopPolling();
  }, []);

  // ── Voice loading ────────────────────────────────────────────────────────

  async function loadVoices() {
    setVoicesLoading(true);
    setVoicesError('');
    try {
      const all = await getTTSVoices();
      setVoices(all);
      // Keep default if present, otherwise first edge voice
      const hasDefault = all.find((v) => v.name === 'en-US-AriaNeural');
      if (!hasDefault) {
        const firstEdge = all.find((v) => v.provider === 'edge');
        if (firstEdge) setSelectedVoice(firstEdge.name);
      }
    } catch {
      setVoicesError('Could not load voice list. Default voice will be used.');
    } finally {
      setVoicesLoading(false);
    }
  }

  // ── Polling ──────────────────────────────────────────────────────────────

  function stopPolling() {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }

  function startPolling(audioId: string) {
    stopPolling();
    pollingRef.current = setInterval(async () => {
      try {
        const updated = await getAudioRecord(audioId);
        setAudio(updated);
        if (updated.status === 'completed' || updated.status === 'failed') {
          stopPolling();
          if (updated.status === 'completed') {
            setPhase('done');
          } else {
            setPhase('error');
            setError(updated.error_message ?? 'Voice generation failed.');
          }
        } else if (updated.status === 'generating') {
          // Optimistically show which step we're on
          setPhase('trying-edge');
        }
      } catch {
        // network glitch — keep polling
      }
    }, POLL_MS);
  }

  // ── Generate ─────────────────────────────────────────────────────────────

  async function handleGenerate() {
    setError('');
    setPhase('trying-edge');
    setAudio(null);

    // Resolve voice: in auto/edge mode use selected Edge voice;
    // in local mode use local-default.
    const voiceToUse = providerMode === 'local' ? 'local-default' : selectedVoice;

    try {
      const record = await generateAudioFromContent(project.id, { voice: voiceToUse });
      setAudio(record);
      if (record.status === 'completed') {
        setPhase('done');
      } else if (record.status === 'failed') {
        setPhase('error');
        setError(record.error_message ?? 'Voice generation failed.');
      } else {
        // Pending/generating — poll
        startPolling(record.id);
      }
    } catch (err) {
      setPhase('error');
      setError(err instanceof Error ? err.message : 'Voice generation failed.');
    }
  }

  // ── Delete ───────────────────────────────────────────────────────────────

  async function handleDelete() {
    if (!audio) return;
    if (!window.confirm('Delete this audio recording?')) return;
    setDeleting(true);
    try {
      await deleteAudioRecord(audio.id);
      setAudio(null);
      setPhase('idle');
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed.');
    } finally {
      setDeleting(false);
    }
  }

  // ── Derived state ─────────────────────────────────────────────────────────
  const isGenerating = phase === 'trying-edge' || phase === 'falling-back' || phase === 'generating-local';
  const isDone = phase === 'done';
  const edgeVoices  = voices.filter((v) => v.provider === 'edge');
  const localVoices = voices.filter((v) => v.provider === 'local');
  const selectedMode = PROVIDER_MODES.find((m) => m.value === providerMode)!;
  const usedProvider = audio?.provider ?? null;

  return (
    <div className="tts-panel">
      <div className="tts-panel__header">
        <span className="tts-panel__title">🎙 Generate Voice</span>
        {isDone && usedProvider && (
          <span className={`tts-provider-badge tts-provider-badge--${usedProvider}`}>
            {PROVIDER_BADGE[usedProvider] ?? usedProvider}
          </span>
        )}
      </div>

      {/* Provider mode selector */}
      <div className="form-group">
        <label className="form-label" htmlFor="tts-mode">TTS Provider</label>
        <select
          id="tts-mode"
          className="form-select"
          value={providerMode}
          disabled={isGenerating}
          onChange={(e) => setProviderMode(e.target.value)}
        >
          {PROVIDER_MODES.map((m) => (
            <option key={m.value} value={m.value}>{m.label}</option>
          ))}
        </select>
        <div className="form-hint form-hint--info">
          {selectedMode.hint}
        </div>
      </div>

      {/* Voice selector — only shown for edge/auto modes */}
      {providerMode !== 'local' && (
        <div className="form-group">
          <label className="form-label" htmlFor="tts-voice">
            Edge Voice
            {voicesLoading && <span className="tts-loading-dot"> …</span>}
          </label>
          {voicesError && (
            <div className="form-hint" style={{ color: 'var(--color-warning)' }}>
              {voicesError}
            </div>
          )}
          <select
            id="tts-voice"
            className="form-select"
            value={selectedVoice}
            disabled={isGenerating || voicesLoading}
            onChange={(e) => setSelectedVoice(e.target.value)}
          >
            {edgeVoices.length === 0 ? (
              <option value="en-US-AriaNeural">en-US-AriaNeural (Aria, Female)</option>
            ) : (
              edgeVoices.map((v) => (
                <option key={v.name} value={v.name}>
                  {v.name} ({v.gender})
                </option>
              ))
            )}
          </select>
        </div>
      )}

      {/* Local voice info */}
      {providerMode === 'local' && localVoices.length > 0 && (
        <div className="tts-local-info">
          <span className="tts-local-info__label">🖥 Local voices:</span>
          {localVoices.map((v) => (
            <span key={v.name} className="tts-local-info__voice">
              {v.language}
            </span>
          ))}
        </div>
      )}

      {/* Duration hint */}
      <div className="form-hint" style={{ marginBottom: '0.75rem' }}>
        Estimated duration:{' '}
        <strong>
          {project.script
            ? formatDuration(project.script.estimated_duration_seconds)
            : '—'}
        </strong>
      </div>

      {/* Generate button */}
      {!isDone && (
        <button
          className="btn btn--primary"
          onClick={handleGenerate}
          disabled={isGenerating}
        >
          {isGenerating ? (
            <>
              <span className="tts-spinner" aria-hidden="true" />
              Generating voice…
            </>
          ) : (
            '🎙 Generate Voice'
          )}
        </button>
      )}

      {/* Live phase indicator */}
      {isGenerating && (
        <div className="tts-phase-log" role="status" aria-live="polite">
          {(providerMode === 'auto' || providerMode === 'edge') && (
            <div className={`tts-phase-step ${phase === 'trying-edge' ? 'tts-phase-step--active' : ''}`}>
              <span className="tts-phase-step__dot" />
              Trying Edge-TTS…
            </div>
          )}
          {phase === 'falling-back' && (
            <div className="tts-phase-step tts-phase-step--warning">
              <span className="tts-phase-step__dot" />
              Edge-TTS temporarily unavailable — switching to Local CPU voice…
            </div>
          )}
          {(phase === 'generating-local' || providerMode === 'local') && (
            <div className={`tts-phase-step ${phase === 'generating-local' ? 'tts-phase-step--active' : ''}`}>
              <span className="tts-phase-step__dot" />
              Generating with Local CPU voice…
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {phase === 'error' && error && (
        <div className="alert alert--error" role="alert" style={{ marginTop: '1rem' }}>
          <strong>Voice generation failed:</strong> {error}
          <br />
          <small style={{ opacity: 0.75 }}>
            {error.includes('403') || error.includes('temporarily unavailable')
              ? 'Tip: Try switching provider to "Local CPU voice" which works offline.'
              : 'Please try again.'}
          </small>
          <div style={{ marginTop: '0.5rem' }}>
            <button className="btn btn--ghost btn--sm" onClick={handleGenerate}>
              Try again
            </button>
          </div>
        </div>
      )}

      {/* Completed result */}
      {isDone && audio && (
        <div className="tts-result">
          {/* Provider used badge */}
          {usedProvider && (
            <div className="tts-result__provider-note">
              ✓ Voice generated using{' '}
              <strong>
                {usedProvider === 'local' ? 'Local CPU voice (fallback)' : 'Edge-TTS'}
              </strong>
            </div>
          )}

          <div className="tts-result__meta">
            <div className="tts-result__item">
              <span className="detail-label">Voice</span>
              <span className="detail-value">{audio.voice}</span>
            </div>
            <div className="tts-result__item">
              <span className="detail-label">Duration</span>
              <span className="detail-value">{formatDuration(audio.duration_seconds)}</span>
            </div>
            <div className="tts-result__item">
              <span className="detail-label">File size</span>
              <span className="detail-value">{formatBytes(audio.file_size_bytes)}</span>
            </div>
            <div className="tts-result__item">
              <span className="detail-label">Format</span>
              <span className="detail-value">
                {audio.audio_url?.includes('.wav') ? 'WAV' : 'MP3'}
              </span>
            </div>
          </div>

          {/* Audio player */}
          <div className="tts-player">
            <audio
              controls
              src={getAudioStreamUrl(audio.id)}
              className="tts-player__audio"
              aria-label="Generated narration audio"
            >
              Your browser does not support the audio element.
            </audio>
          </div>

          {/* Actions */}
          <div className="tts-result__actions">
            <a
              href={getAudioStreamUrl(audio.id)}
              download={`narration_${project.id}`}
              className="btn btn--ghost btn--sm"
            >
              ⬇ Download
            </a>
            <button
              className="btn btn--ghost btn--sm"
              onClick={() => { setPhase('idle'); setAudio(null); }}
            >
              ↺ Regenerate
            </button>
            <button
              className="btn btn--danger btn--sm"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? 'Deleting…' : '🗑 Delete'}
            </button>
          </div>
        </div>
      )}

      <div className="tts-panel__note">
        Auto mode: Edge-TTS (neural quality) with automatic Local CPU fallback.
        Local mode works offline with Windows SAPI voices.
      </div>
    </div>
  );
}
