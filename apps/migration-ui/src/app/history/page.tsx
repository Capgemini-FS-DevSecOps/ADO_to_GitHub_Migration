'use client';

import { useEffect, useState } from 'react';
import { fetchHistory } from '@/lib/api';

export default function HistoryPage() {
  const [sessions, setSessions] = useState<Array<Record<string, unknown>>>([]);

  useEffect(() => {
    fetchHistory().then(setSessions).catch(() => setSessions([]));
  }, []);

  return (
    <main className="oai-page">
      <h1 className="oai-heading">Audit & Session History</h1>
      <p className="oai-muted">Sessions, approvals, and gate overrides (FR-039).</p>
      <ul className="oai-list">
        {sessions.map((s, i) => (
          <li key={i} className="oai-card">
            <code>{String(s.event_type || s.session_id || 'event')}</code>
            <span className="oai-muted">{String(s.created_at || '')}</span>
          </li>
        ))}
      </ul>
    </main>
  );
}
