import { useState } from 'react';
import { generateScript } from '../services/api';
import type { ContentProjectDetail, ScriptRequest } from '../types/api';

interface ScriptGeneratorFormProps {
  onComplete: (project: ContentProjectDetail) => void;
}

type GenerationStep = 'idle' | 'researching' | 'generating' | 'validating' | 'done' | 'error';

const TONES = [
  { value: 'informative',  label: 'Informative' },
  { value: 'engaging',     label: 'Engaging & Conversational' },
  { value: 'educational',  label: 'Educational' },
  { value: 'entertaining', label: 'Entertaining' },
  { value: 'professional', label: 'Professional' },
];

const LANGUAGES = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'pt', label: 'Portuguese' },
];

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s === 0 ? `${m} min` : `${m}m ${s}s`;
}

export function ScriptGeneratorForm({ onComplete }: ScriptGeneratorFormProps) {
  const [topic, setTopic]       = useState('');
  const [language, setLanguage] = useState('en');
  const [tone, setTone]         = useState('informative');
  const [duration, setDuration] = useState(180);
  const [sceneCount, setSceneCount] = useState(12);

  const [step, setStep]         = useState<GenerationStep>('idle');
  const [error, setError]       = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!topic.trim()) return;

    setError('');
    setStep('researching');

    const request: ScriptRequest = {
      topic: topic.trim(),
      language,
      tone,
      target_duration_seconds: duration,
      scene_count: sceneCount,
    };

    try {
      // The backend does research → generate → validate in one call.
      // We optimistically advance the step labels while waiting.
      const timer = setTimeout(() => setStep('generating'), 4000);
      const timer2 = setTimeout(() => setStep('validating'), 12000);

      const project = await generateScript(request);

      clearTimeout(timer);
      clearTimeout(timer2);
      setStep('done');
      onComplete(project);
    } catch (err) {
      setStep('error');
      setError(err instanceof Error ? err.message : 'Generation failed. Please try again.');
    }
  }

  const isLoading = step === 'researching' || step === 'generating' || step === 'validating';
  const stepIndex = (['idle', 'researching', 'generating', 'validating', 'done', 'error'] as const).indexOf(step);

  return (
    <form className="script-form" onSubmit={handleSubmit} noValidate>
      <h2 className="script-form__title">✍ Generate Script</h2>

      {/* Topic */}
      <div className="form-group">
        <label className="form-label" htmlFor="sg-topic">
          Topic <span className="required">*</span>
        </label>
        <input
          id="sg-topic"
          type="text"
          className="form-input"
          placeholder="e.g. Why do cats purr?"
          value={topic}
          maxLength={300}
          disabled={isLoading}
          onChange={(e) => setTopic(e.target.value)}
        />
        <div className="form-hint">{topic.length}/300</div>
      </div>

      {/* Language */}
      <div className="form-group">
        <label className="form-label" htmlFor="sg-language">Language</label>
        <select
          id="sg-language"
          className="form-select"
          value={language}
          disabled={isLoading}
          onChange={(e) => setLanguage(e.target.value)}
        >
          {LANGUAGES.map((l) => (
            <option key={l.value} value={l.value}>{l.label}</option>
          ))}
        </select>
      </div>

      {/* Tone */}
      <div className="form-group">
        <label className="form-label" htmlFor="sg-tone">Tone</label>
        <select
          id="sg-tone"
          className="form-select"
          value={tone}
          disabled={isLoading}
          onChange={(e) => setTone(e.target.value)}
        >
          {TONES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>
      </div>

      {/* Target Duration */}
      <div className="form-group">
        <label className="form-label" htmlFor="sg-duration">
          Target Duration — <strong>{formatDuration(duration)}</strong>
        </label>
        <input
          id="sg-duration"
          type="range"
          className="form-range"
          min={60}
          max={600}
          step={30}
          value={duration}
          disabled={isLoading}
          onChange={(e) => setDuration(Number(e.target.value))}
        />
        <div className="form-hint range-labels">
          <span>1 min</span><span>5 min</span><span>10 min</span>
        </div>
      </div>

      {/* Scene Count */}
      <div className="form-group">
        <label className="form-label" htmlFor="sg-scenes">
          Number of Scenes — <strong>{sceneCount}</strong>
        </label>
        <input
          id="sg-scenes"
          type="range"
          className="form-range"
          min={3}
          max={30}
          step={1}
          value={sceneCount}
          disabled={isLoading}
          onChange={(e) => setSceneCount(Number(e.target.value))}
        />
        <div className="form-hint range-labels">
          <span>3</span><span>16</span><span>30</span>
        </div>
      </div>

      {/* Status indicator */}
      {isLoading && (
        <div className="generation-status">
          <div className="generation-status__spinner" aria-hidden="true" />
          <div className="generation-status__steps">
            <span className={`gen-step ${step === 'researching' ? 'gen-step--active' : stepIndex > 1 ? 'gen-step--done' : ''}`}>
              🔍 Researching topic…
            </span>
            <span className={`gen-step ${step === 'generating' ? 'gen-step--active' : stepIndex > 2 ? 'gen-step--done' : ''}`}>
              ✍ Generating script…
            </span>
            <span className={`gen-step ${step === 'validating' ? 'gen-step--active' : stepIndex > 3 ? 'gen-step--done' : ''}`}>
              ✓ Validating…
            </span>
          </div>
        </div>
      )}

      {/* Error */}
      {step === 'error' && error && (
        <div className="alert alert--error" role="alert">
          <strong>Generation failed:</strong> {error}
        </div>
      )}

      <button
        type="submit"
        className="btn btn--primary btn--full"
        disabled={isLoading || !topic.trim()}
      >
        {isLoading ? 'Generating…' : '🚀 Generate Script'}
      </button>
    </form>
  );
}
