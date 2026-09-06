import { useState } from 'react';
import { AuthStatus } from './components/AuthStatus';
import { UploadForm } from './components/UploadForm';
import { UploadProgress } from './components/UploadProgress';
import { VideoList } from './components/VideoList';
import { AIContentPage } from './components/AIContentPage';
import type { UploadJob } from './types/api';
import './App.css';

type View = 'upload' | 'progress' | 'history' | 'content';

export default function App() {
  const [view, setView]           = useState<View>('upload');
  const [activeJob, setActiveJob] = useState<UploadJob | null>(null);

  function handleJobStarted(job: UploadJob) {
    setActiveJob(job);
    setView('progress');
  }

  function handleUploadComplete(job: UploadJob) {
    setActiveJob(job);
  }

  function handleRetry() {
    setActiveJob(null);
    setView('upload');
  }

  return (
    <div className="app">
      {/* Top bar */}
      <header className="app-header">
        <div className="app-header__inner">
          <div className="app-header__brand">
            <span className="app-header__logo">▶</span>
            <span className="app-header__name">YouTube Uploader</span>
          </div>
          <AuthStatus />
        </div>
      </header>

      {/* Nav */}
      <nav className="app-nav">
        <button
          className={`nav-tab ${view === 'upload' || view === 'progress' ? 'nav-tab--active' : ''}`}
          onClick={handleRetry}
        >
          ⬆ Upload
        </button>
        <button
          className={`nav-tab ${view === 'history' ? 'nav-tab--active' : ''}`}
          onClick={() => setView('history')}
        >
          📋 History
        </button>
        <button
          className={`nav-tab ${view === 'content' ? 'nav-tab--active' : ''}`}
          onClick={() => setView('content')}
        >
          🤖 AI Content
        </button>
      </nav>

      {/* Main content */}
      <main className="app-main">
        {view === 'upload' && (
          <UploadForm onJobStarted={handleJobStarted} />
        )}

        {view === 'progress' && activeJob && (
          <UploadProgress
            job={activeJob}
            onComplete={handleUploadComplete}
            onRetry={handleRetry}
          />
        )}

        {view === 'history' && <VideoList />}

        {view === 'content' && <AIContentPage />}
      </main>

      <footer className="app-footer">
        YouTube Uploader — powered by YouTube Data API v3
      </footer>
    </div>
  );
}
