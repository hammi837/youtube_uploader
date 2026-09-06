import { useState } from 'react';
import { deleteContentProject } from '../services/api';
import type { ContentProjectDetail, Scene } from '../types/api';
import { TTSPanel } from './TTSPanel';

interface ScriptViewerProps {
  project: ContentProjectDetail;
  onBack: () => void;
  onDeleted: (id: string) => void;
}

function formatSeconds(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  if (m === 0) return `${sec}s`;
  if (sec === 0) return `${m}m`;
  return `${m}m ${sec}s`;
}

export function ScriptViewer({ project, onBack, onDeleted }: ScriptViewerProps) {
  const [deleting, setDeleting] = useState(false);
  const [expandAll, setExpandAll] = useState(false);
  const script = project.script;

  async function handleDelete() {
    if (!window.confirm('Delete this content project? This cannot be undone.')) return;
    setDeleting(true);
    try {
      await deleteContentProject(project.id);
      onDeleted(project.id);
    } catch {
      setDeleting(false);
    }
  }

  return (
    <div className="script-viewer">
      {/* Back */}
      <button className="btn btn--ghost btn--sm script-viewer__back" onClick={onBack}>
        ← Back
      </button>

      {/* Status banner for failed projects */}
      {project.status === 'failed' && (
        <div className="alert alert--error" role="alert">
          <strong>Generation failed:</strong> {project.error_message ?? 'Unknown error.'}
        </div>
      )}

      {/* Header */}
      <div className="script-viewer__header">
        <div>
          <h2 className="script-viewer__title">{script?.title ?? project.topic}</h2>
          <div className="script-viewer__topic-label">Topic: {project.topic}</div>
        </div>
        <button
          className="btn btn--danger btn--sm"
          onClick={handleDelete}
          disabled={deleting}
          aria-label="Delete project"
        >
          {deleting ? 'Deleting…' : '🗑 Delete'}
        </button>
      </div>

      {script && (
        <>
          {/* Meta row */}
          <div className="script-meta">
            <div className="script-meta__item">
              <span className="script-meta__label">Duration</span>
              <span className="script-meta__value">{formatSeconds(script.estimated_duration_seconds)}</span>
            </div>
            <div className="script-meta__item">
              <span className="script-meta__label">Scenes</span>
              <span className="script-meta__value">{script.scenes.length}</span>
            </div>
            <div className="script-meta__item">
              <span className="script-meta__label">Language</span>
              <span className="script-meta__value">{project.language.toUpperCase()}</span>
            </div>
            <div className="script-meta__item">
              <span className="script-meta__label">Tone</span>
              <span className="script-meta__value" style={{ textTransform: 'capitalize' }}>{project.tone}</span>
            </div>
          </div>

          {/* Hook */}
          {script.hook && (
            <div className="script-section">
              <div className="script-section__label">🪝 Hook</div>
              <p className="script-section__content hook-text">{script.hook}</p>
            </div>
          )}

          {/* Description */}
          {script.description && (
            <div className="script-section">
              <div className="script-section__label">📝 YouTube Description</div>
              <p className="script-section__content">{script.description}</p>
            </div>
          )}

          {/* Tags */}
          {script.tags.length > 0 && (
            <div className="script-section">
              <div className="script-section__label">🏷 Tags</div>
              <div className="tag-list">
                {script.tags.map((tag) => (
                  <span key={tag} className="tag">{tag}</span>
                ))}
              </div>
            </div>
          )}

          {/* Research sources */}
          {project.sources.length > 0 && (
            <div className="script-section">
              <div className="script-section__label">🔍 Research Sources</div>
              <div className="source-list">
                {project.sources.map((src, i) => (
                  <div key={i} className="source-item">
                    <a
                      href={src.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="source-item__title"
                    >
                      {src.title}
                    </a>
                    {src.snippet && (
                      <p className="source-item__snippet">{src.snippet.slice(0, 200)}{src.snippet.length > 200 ? '…' : ''}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Scenes */}
          <div className="script-section">
            <div className="script-section__header">
              <div className="script-section__label">🎬 Scenes ({script.scenes.length})</div>
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => setExpandAll((v) => !v)}
              >
                {expandAll ? 'Collapse all' : 'Expand all'}
              </button>
            </div>
            <div className="scene-list">
              {script.scenes.map((scene) => (
                <SceneCardControlled
                  key={scene.scene_number}
                  scene={scene}
                  forceExpand={expandAll}
                />
              ))}
            </div>
          </div>

          {/* TTS Voice Generation */}
          <TTSPanel project={project} />
        </>
      )}
    </div>
  );
}

// Controlled scene card that respects forceExpand
function SceneCardControlled({ scene, forceExpand }: { scene: Scene; forceExpand: boolean }) {
  const [localExpanded, setLocalExpanded] = useState(false);
  const expanded = forceExpand || localExpanded;

  return (
    <div className="scene-card">
      <div
        className="scene-card__header"
        onClick={() => setLocalExpanded((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && setLocalExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <div className="scene-card__meta">
          <span className="scene-card__number">Scene {scene.scene_number}</span>
          <span className="scene-card__duration">{formatSeconds(scene.estimated_duration_seconds)}</span>
        </div>
        <p className="scene-card__preview">{scene.narration.slice(0, 120)}{scene.narration.length > 120 ? '…' : ''}</p>
        <span className="scene-card__chevron">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div className="scene-card__body">
          <div className="scene-section">
            <div className="scene-section__label">🎙 Narration</div>
            <p className="scene-section__text">{scene.narration}</p>
          </div>
          <div className="scene-section">
            <div className="scene-section__label">🎬 Visual</div>
            <p className="scene-section__text scene-section__text--muted">{scene.visual_description}</p>
          </div>
        </div>
      )}
    </div>
  );
}
