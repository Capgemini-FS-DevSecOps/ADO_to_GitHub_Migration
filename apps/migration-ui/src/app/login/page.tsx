import { Suspense } from 'react';
import LoginClient from './LoginClient';

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="login-page login-page--loading">
          <div className="login-backdrop" aria-hidden />
          <div className="login-card oai-card login-sequence-shell">
            <div className="login-boot-loader" aria-live="polite">
              <div className="login-boot-loader-bar" />
              <span className="login-boot-loader-text">Starting console…</span>
            </div>
          </div>
        </div>
      }
    >
      <LoginClient />
    </Suspense>
  );
}
