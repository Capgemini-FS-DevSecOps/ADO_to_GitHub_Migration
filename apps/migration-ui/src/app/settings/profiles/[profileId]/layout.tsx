'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { ProfileNav } from '@/components/SettingsNav';
import { fetchMigrationProfile } from '@/lib/api';

export default function ProfileLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: { profileId: string };
}) {
  const { data: profile, isLoading } = useQuery({
    queryKey: ['migration-profile', params.profileId],
    queryFn: () => fetchMigrationProfile(params.profileId),
  });

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="oai-error">
        Migration profile not found. <Link href="/settings/profiles">Back to list</Link>
      </div>
    );
  }

  return (
    <div>
      <ProfileNav profileId={params.profileId} profileName={profile.name} />
      {children}
    </div>
  );
}
