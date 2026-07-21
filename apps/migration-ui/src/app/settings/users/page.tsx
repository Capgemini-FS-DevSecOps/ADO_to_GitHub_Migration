'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ACCEL } from '@/lib/api';
import { fetchSession } from '@/lib/auth';

type PlatformUser = {
  id: string;
  username: string;
  role: string;
  display_name: string;
  status: string;
  created_at?: string;
};

async function apiUsers(path: string, init?: RequestInit) {
  const r = await fetch(`${ACCEL}${path}`, { credentials: 'include', ...init });
  if (!r.ok) {
    const text = await r.text();
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed.detail) throw new Error(parsed.detail);
    } catch (parseErr) {
      if (parseErr instanceof Error && parseErr.message !== text) throw parseErr;
    }
    throw new Error(text || `Request failed (${r.status})`);
  }
  return r.json();
}

function statusLabel(status: string): string {
  if (status === 'pending_approval') return 'Pending approval';
  if (status === 'disabled') return 'Disabled';
  return 'Active';
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
  const [notice, setNotice] = useState('');

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
      setNotice('User created and can sign in immediately.');
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Create failed'),
  });

  const updateMut = useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: Record<string, string> }) =>
      apiUsers(`/v1/auth/users/${userId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['auth-users'] });
      setNotice('User updated.');
      setError('');
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Update failed'),
  });

  const approveMut = useMutation({
    mutationFn: (userId: string) =>
      apiUsers(`/v1/auth/users/${userId}/approve`, { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['auth-users'] });
      setNotice('User approved — they can sign in now.');
      setError('');
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Approve failed'),
  });

  const disableMut = useMutation({
    mutationFn: (userId: string) =>
      apiUsers(`/v1/auth/users/${userId}/disable`, { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['auth-users'] });
      setNotice('User disabled — active sessions were revoked.');
      setError('');
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Disable failed'),
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

  const users = (data?.users ?? []) as PlatformUser[];
  const pending = users.filter((u) => u.status === 'pending_approval');
  const currentUserId = session.user.id;

  return (
    <div>
      <h2 className="oai-subsection-title">Platform users</h2>
      <p className="form-hint">
        Approve self-registrations, adjust roles, or restrict access. Users created here are active
        immediately; self-registered accounts require approval before sign-in.
      </p>
      {notice && <p className="form-hint">{notice}</p>}
      {error && <p className="oai-error">{error}</p>}

      {pending.length > 0 && (
        <div className="oai-card" style={{ marginBottom: 16 }}>
          <h3 className="oai-subsection-title">Pending approval ({pending.length})</h3>
          {pending.map((u) => (
            <div
              key={u.id}
              className="credential-meta"
              style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}
            >
              <p style={{ flex: 1, margin: 0 }}>
                <strong>{u.display_name || u.username}</strong> ({u.username}) — {u.role}
              </p>
              <button
                type="button"
                className="oai-button oai-button-primary"
                disabled={approveMut.isPending}
                onClick={() => approveMut.mutate(u.id)}
              >
                Approve
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="oai-card" style={{ marginBottom: 16 }}>
        <h3 className="oai-subsection-title">All users</h3>
        {users.map((u) => (
          <div
            key={u.id}
            className="credential-meta"
            style={{
              display: 'flex',
              gap: 12,
              alignItems: 'center',
              flexWrap: 'wrap',
              marginBottom: 12,
            }}
          >
            <div style={{ flex: 1, minWidth: 200 }}>
              <strong>{u.display_name || u.username}</strong> ({u.username})
              <span className="form-hint" style={{ marginLeft: 8 }}>
                {statusLabel(u.status)}
              </span>
            </div>
            {u.role === 'admin' ? (
              <span className="form-hint">admin</span>
            ) : (
              <>
                <select
                  className="oai-input"
                  value={u.role}
                  disabled={updateMut.isPending || u.id === currentUserId}
                  onChange={(e) =>
                    updateMut.mutate({ userId: u.id, body: { role: e.target.value } })
                  }
                >
                  <option value="operator">Operator</option>
                  <option value="coordinator">Coordinator</option>
                  <option value="approver">Approver</option>
                </select>
                {u.status === 'pending_approval' ? (
                  <button
                    type="button"
                    className="oai-button oai-button-primary"
                    disabled={approveMut.isPending}
                    onClick={() => approveMut.mutate(u.id)}
                  >
                    Approve
                  </button>
                ) : u.status === 'disabled' ? (
                  <button
                    type="button"
                    className="oai-button"
                    disabled={updateMut.isPending}
                    onClick={() => updateMut.mutate({ userId: u.id, body: { status: 'active' } })}
                  >
                    Enable
                  </button>
                ) : u.id !== currentUserId ? (
                  <button
                    type="button"
                    className="oai-button oai-button-secondary"
                    disabled={disableMut.isPending}
                    onClick={() => disableMut.mutate(u.id)}
                  >
                    Disable
                  </button>
                ) : null}
              </>
            )}
          </div>
        ))}
      </div>

      <div className="oai-card">
        <h3 className="oai-subsection-title">Create user (pre-approved)</h3>
        <div className="form-grid">
          <input
            className="oai-input"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            className="oai-input"
            placeholder="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
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
