import { Suspense } from 'react';
import LoginClient from './LoginClient';

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="login-page oai-loading">
          <div className="oai-spinner" />
        </div>
      }
    >
      <LoginClient />
    </Suspense>
  );
}
