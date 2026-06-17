'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ACCEL } from '@/lib/api';
import { fetchSession } from '@/lib/auth';

async function apiUsers(path: string, init?: RequestInit) {
  const r = await fetch(`${ACCEL}${path}`, { credentials: 'include', ...init });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export default function UsersSettingsPage() {
  const qc = useQueryClient();
  const { data: session } = useQuery({ queryKey: ['session'], queryFn: fetchSession });
  const { data, isLoading } = useQuery({
    queryKey: ['auth-users'],
    queryFn: () => apiUsers('/v1/auth/users'),
    enabled: session?.user?.role === 'admin',
  });

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState('operator');
  const [error, setError] = useState('');

  const createMut = useMutation({
    mutationFn: () =>
      apiUsers('/v1/auth/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username,
          password,
          display_name: displayName,
          role,
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['auth-users'] });
      setUsername('');
      setPassword('');
      setDisplayName('');
      setError('');
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Create failed'),
  });

  if (session?.user?.role !== 'admin') {
    return <p className="form-hint">Admin only — user management is restricted to platform administrators.</p>;
  }

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  return (
    <div>
      <h2 className="oai-subsection-title">Platform users</h2>
      <div className="oai-card" style={{ marginBottom: 16 }}>
        {(data?.users ?? []).map((u: { id: string; username: string; role: string; display_name: string }) => (
          <p key={u.id} className="credential-meta">
            <strong>{u.display_name || u.username}</strong> ({u.username}) — {u.role}
          </p>
        ))}
      </div>
      <div className="oai-card">
        <h3 className="oai-subsection-title">Create user</h3>
        {error && <p className="oai-error">{error}</p>}
        <div className="form-grid">
          <input className="oai-input" placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
          <input className="oai-input" placeholder="Display name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          <select className="oai-input" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="operator">Operator</option>
            <option value="coordinator">Coordinator</option>
            <option value="approver">Approver</option>
          </select>
          <input
            className="oai-input"
            type="password"
            placeholder="Password (12+ chars)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <button
          type="button"
          className="oai-button oai-button-primary"
          style={{ marginTop: 12 }}
          disabled={createMut.isPending}
          onClick={() => createMut.mutate()}
        >
          Create user
        </button>
      </div>
    </div>
  );
}
