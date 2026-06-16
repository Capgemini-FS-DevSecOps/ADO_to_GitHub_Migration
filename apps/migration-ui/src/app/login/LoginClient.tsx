'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  bootstrapAdmin,
  fetchBootstrapStatus,
  login,
  REQUIRE_AUTH,
} from '@/lib/auth';
import { ACCEL } from '@/lib/api';

export default function LoginClient() {
  const router = useRouter();
  const params = useSearchParams();
  const bootstrapMode = params.get('bootstrap') === '1';
  const apiError = params.get('error') === 'api';

  const [needsBootstrap, setNeedsBootstrap] = useState(bootstrapMode);
  const [username, setUsername] = useState('admin');
  const [displayName, setDisplayName] = useState('Platform Admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!REQUIRE_AUTH) {
      router.replace('/');
      return;
    }
    fetchBootstrapStatus()
      .then((s) => {
        setNeedsBootstrap(s.needs_bootstrap);
        setLoading(false);
      })
      .catch(() => {
        setError(`Cannot reach API at ${ACCEL}. Start docker compose or run-local.ps1.`);
        setLoading(false);
      });
  }, [router]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      if (needsBootstrap) {
        await bootstrapAdmin({ username, password, display_name: displayName });
      } else {
        await login({ username, password });
      }
      router.replace('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed');
    }
  };

  if (loading) {
    return (
      <div className="login-page">
        <div className="oai-loading">
          <div className="oai-spinner" />
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-card oai-card">
        <div className="oai-logo login-logo" aria-hidden>ADO</div>
        <h1 className="oai-page-title">
          {needsBootstrap ? 'Create admin account' : 'Sign in'}
        </h1>
        <p className="login-subtitle">
          {needsBootstrap
            ? 'First boot — create the platform administrator to continue.'
            : 'ADO2GH Migration Console'}
        </p>
        {apiError && (
          <p className="oai-error">Session expired or API unreachable. Sign in again after the stack is up.</p>
        )}
        {error && <p className="oai-error">{error}</p>}
        <form onSubmit={submit} className="login-form">
          {needsBootstrap && (
            <label className="oai-field">
              Display name
              <input
                className="oai-input"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </label>
          )}
          <label className="oai-field">
            Username
            <input
              className="oai-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="oai-field">
            Password
            <input
              className="oai-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={needsBootstrap ? 'new-password' : 'current-password'}
              required
              minLength={needsBootstrap ? 12 : 1}
            />
          </label>
          {needsBootstrap && (
            <p className="login-hint">Minimum 12 characters for the admin password.</p>
          )}
          <button type="submit" className="oai-button oai-button-primary login-submit">
            {needsBootstrap ? 'Create admin & continue' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
}
