'use client';

import { useEffect, useState } from 'react';
import { listAssignments } from '@/lib/api';

export default function AssignmentsPage() {
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const profileId = 'default';

  useEffect(() => {
    listAssignments(profileId).then(setItems).catch(() => setItems([]));
  }, []);

  return (
    <main className="oai-page">
      <h1 className="oai-heading">Migration Assignments</h1>
      <p className="oai-muted">Coordinator-managed cohorts for POC, pilot, and waves.</p>
      <ul className="oai-list">
        {items.map((a) => (
          <li key={String(a.id)} className="oai-card">
            <strong>{String(a.name)}</strong>
            <span className="oai-badge">{String(a.assignment_type)}</span>
            <span>{String(a.repo_count)} repos</span>
          </li>
        ))}
      </ul>
    </main>
  );
}
