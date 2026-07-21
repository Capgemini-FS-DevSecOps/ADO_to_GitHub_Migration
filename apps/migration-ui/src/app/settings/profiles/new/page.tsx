'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { ProfileWizard } from '@/components/ProfileWizard';
import { fetchOnboardingStatus } from '@/lib/api';
import { fetchSession } from '@/lib/auth';

export default function NewMigrationProfilePage() {
  const { data: session } = useQuery({ queryKey: ['session'], queryFn: fetchSession });
  const { data: onboarding, isLoading } = useQuery({
    queryKey: ['onboarding'],
    queryFn: fetchOnboardingStatus,
  });

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  if (!onboarding?.can_submit_profile) {
    return (
      <div className="oai-card">
        <p>An administrator must create the first active deployment profile before operators can submit profiles.</p>
        <Link href="/settings/profiles" className="oai-button oai-button-secondary" style={{ marginTop: 12 }}>
          Back to profiles
        </Link>
      </div>
    );
  }

  const mode =
    session?.user?.role === 'admin' ? 'settings-admin' : 'settings-operator';

  return <ProfileWizard mode={mode} />;
}
