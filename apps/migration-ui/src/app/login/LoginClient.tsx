'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  bootstrapAdmin,
  fetchBootstrapStatus,
  login,
  register,
  REQUIRE_AUTH,
} from '@/lib/auth';
import { ACCEL } from '@/lib/api';

export default function LoginClient() {
  const router = useRouter();
  const params = useSearchParams();
  const bootstrapMode = params.get('bootstrap') === '1';
  const registerMode = params.get('register') === '1';
  const returnUrl = params.get('returnUrl') || '';
  const apiError = params.get('error') === 'api';

  const [needsBootstrap, setNeedsBootstrap] = useState(bootstrapMode);
  const [registrationEnabled, setRegistrationEnabled] = useState(false);
  const [showRegister, setShowRegister] = useState(registerMode);
  const [username, setUsername] = useState('admin');
  const [displayName, setDisplayName] = useState('Platform Admin');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
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
        setRegistrationEnabled(Boolean(s.registration_enabled));
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
    if ((needsBootstrap || showRegister) && password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    try {
      let result;
      if (needsBootstrap) {
        result = await bootstrapAdmin({ username, password, display_name: displayName });
      } else if (showRegister) {
        result = await register({ username, password, display_name: displayName });
      } else {
        result = await login({ username, password });
      }
      const redirect = result.redirect_path as string | undefined;
      const target = redirect || returnUrl || '/';
      router.replace(target.startsWith('/') ? target : '/');
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

  const title = needsBootstrap
    ? 'Create admin account'
    : showRegister
      ? 'Create operator account'
      : 'Sign in';

  return (
    <div className="login-page">
      <div className="login-card oai-card">
        <div className="oai-logo login-logo" aria-hidden>ADO</div>
        <h1 className="oai-page-title">{title}</h1>
        <p className="login-subtitle">
          {needsBootstrap
            ? 'First boot — create the platform administrator to continue.'
            : showRegister
              ? 'Self-register as an operator (pending profile approval applies).'
              : 'ADO2GH Migration Console'}
        </p>
        {apiError && (
          <p className="oai-error">Session expired or API unreachable. Sign in again after the stack is up.</p>
        )}
        {error && <p className="oai-error">{error}</p>}
        <form onSubmit={submit} className="login-form">
          {(needsBootstrap || showRegister) && (
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
              autoComplete={needsBootstrap || showRegister ? 'new-password' : 'current-password'}
              required
              minLength={needsBootstrap || showRegister ? 12 : 1}
            />
          </label>
          {(needsBootstrap || showRegister) && (
            <label className="oai-field">
              Confirm password
              <input
                className="oai-input"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
                required
                minLength={12}
              />
            </label>
          )}
          {(needsBootstrap || showRegister) && (
            <p className="login-hint">Minimum 12 characters.</p>
          )}
          <button type="submit" className="oai-button oai-button-primary login-submit">
            {needsBootstrap ? 'Create admin & continue' : showRegister ? 'Create account' : 'Sign in'}
          </button>
        </form>
        {!needsBootstrap && registrationEnabled && (
          <p className="login-hint" style={{ marginTop: 16 }}>
            {showRegister ? (
              <button type="button" className="oai-button oai-button-secondary" onClick={() => setShowRegister(false)}>
                Back to sign in
              </button>
            ) : (
              <button type="button" className="oai-button oai-button-secondary" onClick={() => setShowRegister(true)}>
                Create account
              </button>
            )}
          </p>
        )}
      </div>
    </div>
  );
}
