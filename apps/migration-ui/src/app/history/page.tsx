'use client';

import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import {
  downloadAuditHistoryExport,
  fetchAuditEventTypes,
  fetchHistory,
  fetchSettings,
} from '@/lib/api';
import { fetchSession } from '@/lib/auth';
import { canViewAllAuditHistory } from '@/lib/permissions';
import type { AuditEvent } from '@/lib/types';

const PAGE_SIZE = 20;

function formatTimestamp(iso: string): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function summarizePayload(raw: string | undefined): string | null {
  if (!raw || raw === '{}') return null;
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const keys = Object.keys(parsed);
    if (!keys.length) return null;
    return keys.slice(0, 4).map((k) => `${k}: ${String(parsed[k])}`).join(' · ');
  } catch {
    return raw.length > 120 ? `${raw.slice(0, 120)}…` : raw;
  }
}

export default function HistoryPage() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });
  const { data: authSession } = useQuery({ queryKey: ['session'], queryFn: fetchSession });
  const profileId = settings?.active_profile_id ?? '';
  const canViewAll = canViewAllAuditHistory(
    authSession?.permissions,
    authSession?.user?.role,
  );

  const [page, setPage] = useState(0);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [actor, setActor] = useState('');
  const [eventType, setEventType] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [scopeAll, setScopeAll] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    setPage(0);
  }, [search, actor, eventType, dateFrom, dateTo, scopeAll, profileId]);

  useEffect(() => {
    if (!canViewAll) {
      setScopeAll(false);
      setActor('');
    }
  }, [canViewAll]);

  const queryParams = useMemo(
    () => ({
      profileId: canViewAll && scopeAll ? undefined : profileId || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      search: search || undefined,
      actor: canViewAll && actor ? actor : undefined,
      eventType: eventType || undefined,
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
    }),
    [canViewAll, scopeAll, profileId, page, search, actor, eventType, dateFrom, dateTo],
  );

  const { data, isLoading, error } = useQuery({
    queryKey: ['audit-history', queryParams],
    queryFn: () => fetchHistory(queryParams),
  });

  const { data: eventTypes = [] } = useQuery({
    queryKey: ['audit-event-types', canViewAll && scopeAll ? 'all' : profileId, canViewAll],
    queryFn: () => fetchAuditEventTypes(canViewAll && scopeAll ? undefined : profileId || undefined),
  });

  const active = settings?.migration_profiles?.find((p) => p.id === profileId);
  const sessions = data?.sessions ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleExport = async () => {
    setExporting(true);
    setExportError(null);
    try {
      await downloadAuditHistoryExport({
        profileId: canViewAll && scopeAll ? undefined : profileId || undefined,
        search: search || undefined,
        actor: canViewAll && actor ? actor : undefined,
        eventType: eventType || undefined,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
      });
    } catch (e) {
      setExportError(e instanceof Error ? e.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  };

  const clearFilters = () => {
    setSearchInput('');
    setSearch('');
    setActor('');
    setEventType('');
    setDateFrom('');
    setDateTo('');
    setPage(0);
  };

  const hasFilters = Boolean(search || actor || eventType || dateFrom || dateTo);

  return (
    <main className="oai-page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <h1 className="oai-heading">Audit & Session History</h1>
          <p className="oai-muted">
            Platform audit trail: logins, profile changes, assignments, agent sessions, and gate overrides.
            {!canViewAll && authSession?.user ? (
              <> Showing your activity only (<strong>{authSession.user.display_name}</strong>).</>
            ) : null}
          </p>
        </div>
        <button
          type="button"
          className="oai-button oai-button-secondary"
          disabled={exporting}
          onClick={handleExport}
        >
          {exporting ? 'Exporting…' : 'Export CSV'}
        </button>
      </div>

      {exportError && <p className="badge-manual" style={{ marginTop: 8 }}>{exportError}</p>}

      <div className="oai-card" style={{ marginTop: 16, marginBottom: 16 }}>
        <div className="history-filters">
          <label className="history-filter-field history-filter-search">
            <span>Search</span>
            <input
              className="oai-input"
              type="search"
              placeholder="Search action, user, or payload…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
          </label>
          {canViewAll ? (
            <label className="history-filter-field">
              <span>User</span>
              <input
                className="oai-input"
                type="text"
                placeholder="Actor username"
                value={actor}
                onChange={(e) => setActor(e.target.value)}
              />
            </label>
          ) : null}
          <label className="history-filter-field">
            <span>Action</span>
            <select
              className="oai-input"
              value={eventType}
              onChange={(e) => setEventType(e.target.value)}
            >
              <option value="">All actions</option>
              {eventTypes.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="history-filter-field">
            <span>From</span>
            <input
              className="oai-input"
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </label>
          <label className="history-filter-field">
            <span>To</span>
            <input
              className="oai-input"
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </label>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
          {canViewAll ? (
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <input
                type="checkbox"
                checked={scopeAll}
                onChange={(e) => setScopeAll(e.target.checked)}
              />
              Show all profiles
            </label>
          ) : null}
          {(canViewAll ? !scopeAll : true) && profileId && active && (
            <span className="form-hint" style={{ margin: 0 }}>
              Profile: <strong>{active.name}</strong>
            </span>
          )}
          {hasFilters && (
            <button type="button" className="oai-button oai-button-secondary" onClick={clearFilters}>
              Clear filters
            </button>
          )}
        </div>
      </div>

      {isLoading && <p className="oai-loading">Loading history…</p>}
      {error && (
        <p className="badge-manual">
          {error instanceof Error ? error.message : 'Failed to load history'}
        </p>
      )}

      {!isLoading && sessions.length === 0 && !error && (
        <div className="oai-card oai-welcome-card">
          <p>
            {hasFilters
              ? 'No events match the current filters.'
              : 'No audit events yet. Events appear after sign-in, profile setup, discovery scans, agent sessions, and migration runs.'}
          </p>
        </div>
      )}

      {sessions.length > 0 && (
        <div className="oai-card table-responsive">
          <table className="history-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>User</th>
                <th>Profile</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((event: AuditEvent) => {
                const details = summarizePayload(event.payload_json);
                return (
                  <tr key={event.id}>
                    <td className="history-cell-muted">{formatTimestamp(event.created_at)}</td>
                    <td><code>{event.event_type}</code></td>
                    <td>{event.actor || '—'}</td>
                    <td className="history-cell-muted">{event.profile_id}</td>
                    <td className="history-cell-muted">{details ?? '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {total > 0 && (
        <div className="history-pagination">
          <span className="form-hint">
            {total} event{total === 1 ? '' : 's'}
            {hasFilters ? ' (filtered)' : ''}
            {' · '}
            Page {page + 1} of {totalPages}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="oai-button oai-button-secondary"
              disabled={page <= 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </button>
            <button
              type="button"
              className="oai-button oai-button-secondary"
              disabled={page + 1 >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
