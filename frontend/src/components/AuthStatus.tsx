import { useEffect, useState } from 'react';
import { getAuthStatus } from '../services/api';
import type { AuthStatus as AuthStatusType } from '../types/api';

export function AuthStatus() {
  const [status, setStatus] = useState<AuthStatusType | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getAuthStatus()
      .then(setStatus)
      .catch(() => setStatus({ authenticated: false, message: 'Could not reach backend.' }))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="auth-status auth-status--loading">Checking connection…</div>;
  }

  return (
    <div className={`auth-status ${status?.authenticated ? 'auth-status--ok' : 'auth-status--warn'}`}>
      {status?.authenticated ? '✓ YouTube connected' : '⚠ YouTube not connected'}
      <span className="auth-status__msg">{status?.message}</span>
    </div>
  );
}
