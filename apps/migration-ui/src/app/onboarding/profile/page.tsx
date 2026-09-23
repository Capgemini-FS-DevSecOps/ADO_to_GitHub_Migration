'use client';

import { ProfileWizard } from '@/components/ProfileWizard';

/** Onboarding step where an administrator creates the first active deployment profile. */
export default function OnboardingProfilePage() {
  return (
    <div>
      <h1 className="oai-page-title">Deployment profile setup</h1>
      <p className="form-hint" style={{ marginBottom: 16 }}>
        Create the first active deployment profile before using the migration console.
      </p>
      <ProfileWizard mode="onboarding" />
    </div>
  );
}
