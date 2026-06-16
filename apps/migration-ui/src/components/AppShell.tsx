'use client';

import { usePathname } from 'next/navigation';
import { NavTabs } from '@/components/NavTabs';
import { AuthGate } from '@/components/AuthGate';

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === '/login';

  if (isLogin) {
    return <>{children}</>;
  }

  return (
    <AuthGate>
      <div className="oai-app">
        <header className="oai-header">
          <div className="oai-container oai-header-inner">
            <div className="oai-logo" aria-hidden>
              ADO
            </div>
            <div className="oai-header-text">
              <h1 className="oai-title">ADO2GH Migration Console</h1>
              <p className="oai-description">
                Enterprise Azure DevOps to GitHub migration accelerator with phased rollout,
                pipeline transformation, PEV agent orchestration, and commit-level validation.
              </p>
              <p className="oai-tech-stack">
                Powered by <strong>ado2gh Accelerator</strong> • <strong>FastAPI</strong> •{' '}
                <strong>Planner–Executor–Validator Agent</strong> | Targets:{' '}
                <strong>GitHub Actions</strong> • <strong>Git mirror / GEI</strong>
              </p>
            </div>
          </div>
          <div className="oai-container">
            <NavTabs />
          </div>
        </header>

        <main className="oai-container oai-main">{children}</main>

        <footer className="oai-footer">
          <div className="oai-container">
            <p>
              ADO2GH Migration Accelerator — OrchestrateAI (OAI) UI • Local: SQLite • Production:
              PostgreSQL / DynamoDB
            </p>
          </div>
        </footer>
      </div>
    </AuthGate>
  );
}
