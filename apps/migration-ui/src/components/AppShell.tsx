'use client';

import { usePathname } from 'next/navigation';
import { UnifiedNavigation } from '@/components/UnifiedNavigation';
import { AuthGate } from '@/components/AuthGate';
import { UserSessionBar } from '@/components/UserSessionBar';
import { BrandLogo, BrandWordmark } from '@/components/BrandLogo';

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === '/login' || pathname.startsWith('/onboarding');

  if (isLogin) {
    return <>{children}</>;
  }

  return (
    <AuthGate>
      <div className="oai-app">
        <header className="oai-header">
          <div className="oai-container oai-header-inner">
            <div className="oai-brand">
              <BrandLogo size={44} />
              <div className="oai-brand-text">
                <BrandWordmark />
                <p className="oai-brand-tagline">
                  Enterprise Azure DevOps → GitHub migration with phased rollout and agent orchestration
                </p>
              </div>
            </div>
            <UserSessionBar />
          </div>
          <div className="oai-container oai-header-nav">
            <UnifiedNavigation />
          </div>
        </header>

        <main className="oai-container oai-main">{children}</main>

        <footer className="oai-footer">
          <div className="oai-container oai-footer-inner">
            <p className="oai-footer-brand">
              <strong>ADO2GitHub</strong> · OrchestrateAI console
            </p>
            <p className="oai-footer-meta">
              ado2gh Accelerator · FastAPI · Planner–Executor–Validator agent · Git mirror / GEI
            </p>
          </div>
        </footer>
      </div>
    </AuthGate>
  );
}
