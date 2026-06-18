'use client';

import { FormEvent, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  bootstrapAdmin,
  fetchBootstrapStatus,
  fetchSession,
  login,
  register,
} from '@/lib/auth';
import { ACCEL } from '@/lib/api';
import { BrandLogo, BrandWordmark } from '@/components/BrandLogo';

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
  const [bootReady, setBootReady] = useState(false);

  useEffect(() => {
    fetchBootstrapStatus()
      .then(async (s) => {
        setNeedsBootstrap(s.needs_bootstrap);
        setRegistrationEnabled(Boolean(s.registration_enabled));
        const session = await fetchSession();
        if (session?.authenticated) {
          router.replace('/');
          return;
        }
        setLoading(false);
      })
      .catch(() => {
        setError(`Cannot reach API at ${ACCEL}. Start docker compose or run-local.ps1.`);
        setLoading(false);
      });
  }, [router]);

  useEffect(() => {
    if (loading) {
      setBootReady(false);
      return;
    }
    const frame = requestAnimationFrame(() => {
      setBootReady(true);
    });
    return () => cancelAnimationFrame(frame);
  }, [loading, needsBootstrap, showRegister]);

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

  const isFirstBoot = needsBootstrap;
  const pageClass = [
    'login-page',
    loading ? 'login-page--loading' : 'login-page--loaded',
    bootReady ? 'login-page--ready' : '',
    isFirstBoot ? 'login-page--first-boot' : 'login-page--returning',
    showRegister && !isFirstBoot ? 'login-page--register' : '',
  ]
    .filter(Boolean)
    .join(' ');

  const title = needsBootstrap
    ? 'Create admin account'
    : showRegister
      ? 'Create operator account'
      : 'Sign in';

  const welcomeLine = isFirstBoot
    ? 'Welcome'
    : showRegister
      ? 'Join the console'
      : 'Welcome back';

  const welcomeSub = isFirstBoot
    ? 'First-time setup for your enterprise migration platform.'
    : showRegister
      ? 'Register as an operator to request profile access.'
      : 'Sign in to continue managing your ADO → GitHub migration.';

  return (
    <div className={pageClass}>
      <div className="login-backdrop" aria-hidden />
      <div className="login-card oai-card login-sequence-shell">
        <header className={`login-brand login-sequence login-sequence--brand${loading ? ' login-sequence--hidden' : ''}`}>
          <BrandLogo size={48} className="login-brand-logo" />
          <div className="login-brand-copy">
            <BrandWordmark layout="stacked" className="login-brand-wordmark" />
          </div>
        </header>

        <div className={`login-sequence login-sequence--welcome${loading ? ' login-sequence--hidden' : ''}`}>
          <p className="login-welcome">{welcomeLine}</p>
          <p className="login-welcome-sub">{welcomeSub}</p>
        </div>

        {loading ? (
          <div className="login-boot-loader login-sequence" aria-live="polite">
            <div className="login-boot-loader-bar" />
            <span className="login-boot-loader-text">Starting console…</span>
          </div>
        ) : (
          <>
            <div className="login-sequence login-sequence--heading">
              <h1 className="login-title">{title}</h1>
              <p className="login-subtitle">
                {needsBootstrap
                  ? 'Create the platform administrator to unlock discovery, migration, and agent workflows.'
                  : showRegister
                    ? 'Self-register as an operator. Profile approval may be required.'
                    : 'Enter your credentials to open the migration console.'}
              </p>
            </div>

            {(apiError || error) && (
              <div className="login-sequence login-sequence--alert">
                {apiError && (
                  <p className="oai-error">
                    Session expired or API unreachable. Sign in again after the stack is up.
                  </p>
                )}
                {error && <p className="oai-error">{error}</p>}
              </div>
            )}

            <form onSubmit={submit} className="login-form login-sequence login-sequence--form">
              {(needsBootstrap || showRegister) && (
                <label className="oai-field login-field login-sequence login-sequence--field-1">
                  Display name
                  <input
                    className="oai-input"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                  />
                </label>
              )}
              <label
                className={`oai-field login-field login-sequence login-sequence--field-${
                  needsBootstrap || showRegister ? '2' : '1'
                }`}
              >
                Username
                <input
                  className="oai-input"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  required
                />
              </label>
              <label
                className={`oai-field login-field login-sequence login-sequence--field-${
                  needsBootstrap || showRegister ? '3' : '2'
                }`}
              >
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
                <label className="oai-field login-field login-sequence login-sequence--field-4">
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
                <p className="login-hint login-sequence login-sequence--hint">Minimum 12 characters.</p>
              )}
              <button
                type="submit"
                className="oai-button oai-button-primary login-submit login-sequence login-sequence--submit"
              >
                {needsBootstrap ? 'Create admin & continue' : showRegister ? 'Create account' : 'Sign in'}
              </button>
            </form>

            {!needsBootstrap && registrationEnabled && (
              <p className="login-hint login-sequence login-sequence--footer">
                {showRegister ? (
                  <button
                    type="button"
                    className="oai-button oai-button-secondary"
                    onClick={() => {
                      setShowRegister(false);
                      setError('');
                    }}
                  >
                    Back to sign in
                  </button>
                ) : (
                  <button
                    type="button"
                    className="oai-button oai-button-secondary"
                    onClick={() => {
                      setShowRegister(true);
                      setError('');
                    }}
                  >
                    Create account
                  </button>
                )}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
