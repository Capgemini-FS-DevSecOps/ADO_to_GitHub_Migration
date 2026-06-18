'use client';

import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { fetchSettings, listAssignments } from '@/lib/api';

export default function AssignmentsPage() {
  const { data: settings, isLoading: settingsLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
  });
  const profileId = settings?.active_profile_id ?? '';
  const active = settings?.migration_profiles?.find((p) => p.id === profileId);

  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!profileId) {
      setItems([]);
      return;
    }
    setLoading(true);
    setError(null);
    listAssignments(profileId)
      .then(setItems)
      .catch((e) => {
        setItems([]);
        setError(e instanceof Error ? e.message : 'Failed to load assignments');
      })
      .finally(() => setLoading(false));
  }, [profileId]);

  return (
    <main className="oai-page">
      <h1 className="oai-heading">Migration Assignments</h1>
      <p className="oai-muted">
        Coordinator-managed cohorts for POC, pilot, and waves — scoped to the active migration profile.
      </p>

      {settingsLoading && <p className="oai-loading">Loading profile…</p>}

      {!settingsLoading && !profileId && (
        <div className="oai-card oai-welcome-card">
          <p>
            No active migration profile.{' '}
            <a href="/settings/profiles">Create or activate a profile</a> first.
          </p>
        </div>
      )}

      {profileId && active && (
        <p className="form-hint" style={{ marginBottom: 12 }}>
          Profile: <strong>{active.name}</strong>
        </p>
      )}

      {error && <p className="badge-manual">{error}</p>}

      {loading && profileId && <p className="oai-loading">Loading assignments…</p>}

      {!loading && profileId && items.length === 0 && !error && (
        <div className="oai-card oai-welcome-card">
          <p>
            No assignments yet for this profile. Coordinators create cohorts via the API{' '}
            <code>POST /v1/profiles/{'{profile_id}'}/assignments</code> or use the Agent to plan
            a migration, then assign repos on the <a href="/discovery">Discovery</a> tab.
          </p>
        </div>
      )}

      <ul className="oai-list">
        {items.map((a) => (
          <li key={String(a.id)} className="oai-card">
            <strong>{String(a.name)}</strong>
            <span className="oai-badge">{String(a.assignment_type)}</span>
            <span>{String(a.repo_count ?? 0)} repos</span>
            {a.execution_phase_id != null && (
              <span className="oai-muted"> · phase {String(a.execution_phase_id)}</span>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
