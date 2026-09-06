import { useState } from 'react';
import type { ContentProjectDetail } from '../types/api';
import { ContentList } from './ContentList';
import { ScriptGeneratorForm } from './ScriptGeneratorForm';
import { ScriptViewer } from './ScriptViewer';

type ContentView = 'generate' | 'result' | 'history';

export function AIContentPage() {
  const [view, setView]           = useState<ContentView>('generate');
  const [result, setResult]       = useState<ContentProjectDetail | null>(null);
  const [historyKey, setHistoryKey] = useState(0); // increment to force reload

  function handleComplete(project: ContentProjectDetail) {
    setResult(project);
    setView('result');
  }

  function handleResultDeleted(_id: string) {
    setResult(null);
    setHistoryKey((k) => k + 1);
    setView('history');
  }

  return (
    <div className="ai-content-page">
      {/* Sub-nav */}
      <div className="content-subnav">
        <button
          className={`nav-tab ${view === 'generate' || view === 'result' ? 'nav-tab--active' : ''}`}
          onClick={() => setView('generate')}
        >
          ✍ Generate
        </button>
        <button
          className={`nav-tab ${view === 'history' ? 'nav-tab--active' : ''}`}
          onClick={() => { setView('history'); setHistoryKey((k) => k + 1); }}
        >
          📋 Script History
        </button>
      </div>

      {/* Content */}
      {view === 'generate' && (
        <ScriptGeneratorForm onComplete={handleComplete} />
      )}

      {view === 'result' && result && (
        <ScriptViewer
          project={result}
          onBack={() => setView('generate')}
          onDeleted={handleResultDeleted}
        />
      )}

      {view === 'history' && (
        <ContentList key={historyKey} />
      )}
    </div>
  );
}
