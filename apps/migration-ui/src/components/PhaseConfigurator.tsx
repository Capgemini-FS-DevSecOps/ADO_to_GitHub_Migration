'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { fetchPhases, fetchProfileScanStatus, fetchSettings, updatePhases } from '@/lib/api';
import type { PhaseDefinition, PhasesPayload, PhaseRemoval } from '@/lib/types';

type EditablePhase = PhaseDefinition & { risk_min?: number };

type PendingSave = { span_to_scan: boolean };

function RescanConfirmModal({
  profileName,
  spanToScan,
  onConfirm,
  onCancel,
}: {
  profileName: string;
  spanToScan: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="phase-modal-backdrop">
      <div className="oai-card phase-modal">
        <h3 className="oai-subsection-title">Re-scan and re-assign repos?</h3>
        <p style={{ fontSize: 13, lineHeight: 1.6 }}>
          Saving {spanToScan ? 'with updated risk bands' : 'these phase settings'} will{' '}
          <strong>re-scan {profileName || 'the active profile'}</strong> and automatically
          re-assign every repo to phases based on the new risk score bands and repo caps.
        </p>
        <p style={{ fontSize: 13, lineHeight: 1.6, color: '#ffc866' }}>
          Any manual phase assignments you made on the Discovery tab will be overwritten.
        </p>
        <p style={{ fontSize: 12, color: '#888' }}>
          The re-scan is read-only on Azure DevOps — no changes are written to ADO.
        </p>
        <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
          <button type="button" className="oai-button oai-button-primary" onClick={onConfirm}>
            Save and re-scan
          </button>
          <button type="button" className="oai-button oai-button-secondary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

function RemovalModal({
  phase,
  targets,
  onConfirm,
  onCancel,
}: {
  phase: EditablePhase;
  targets: EditablePhase[];
  onConfirm: (targetId: string) => void;
  onCancel: () => void;
}) {
  const [target, setTarget] = useState(targets[0]?.id ?? '');
  return (
    <div className="phase-modal-backdrop">
      <div className="oai-card phase-modal">
        <h3 className="oai-subsection-title">Move repos before removing</h3>
        <p style={{ fontSize: 13 }}>
          Phase <strong>{phase.name}</strong> has{' '}
          <strong>{phase.repo_count ?? 0}</strong> assigned repo(s). Choose another phase to move
          them to:
        </p>
        <select className="oai-input" value={target} onChange={(e) => setTarget(e.target.value)}>
          {targets.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
          <button type="button" className="oai-button oai-button-primary" onClick={() => onConfirm(target)}>
            Move & remove
          </button>
          <button type="button" className="oai-button oai-button-secondary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

export function PhaseConfigurator({ profileId }: { profileId?: string | null }) {
  const qc = useQueryClient();
  const { data: payload, isLoading } = useQuery({
    queryKey: ['phases', profileId],
    queryFn: () => fetchPhases(profileId ?? undefined),
  });
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings });

  const [rows, setRows] = useState<EditablePhase[]>([]);
  const [pendingRemoval, setPendingRemoval] = useState<EditablePhase | null>(null);
  const [removals, setRemovals] = useState<PhaseRemoval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [rescanNote, setRescanNote] = useState<string | null>(null);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
  const [bgScanProfileId, setBgScanProfileId] = useState<string | null>(null);

  useEffect(() => {
    if (payload?.phases) {
      setRows(payload.phases.map((p) => ({ ...p, repo_count: payload.repo_counts_by_phase[p.id] })));
      setRemovals([]);
    }
  }, [payload]);

  const { data: scanStatus } = useQuery({
    queryKey: ['profile-scan-status', bgScanProfileId],
    queryFn: () => fetchProfileScanStatus(bgScanProfileId!),
    enabled: !!bgScanProfileId,
    refetchInterval: (query) => (query.state.data?.running ? 2000 : false),
  });

  useEffect(() => {
    if (!bgScanProfileId || scanStatus === undefined) return;
    if (scanStatus.running) return;
    setBgScanProfileId(null);
    setRescanNote('Phases saved and profile re-scanned.');
    qc.invalidateQueries({ queryKey: ['phases'] });
    qc.invalidateQueries({ queryKey: ['settings'] });
    qc.invalidateQueries({ queryKey: ['discovery'] });
    qc.invalidateQueries({ queryKey: ['profile-scan'] });
  }, [bgScanProfileId, scanStatus, qc]);

  const saveMut = useMutation({
    mutationFn: (body: { span_to_scan?: boolean }) =>
      updatePhases({
        phases: rows.map(({ id, name, risk_max, repo_cap, order }) => ({
          id,
          name,
          risk_max: Number(risk_max),
          repo_cap: Number(repo_cap),
          order,
        })),
        removals,
        span_to_scan: body.span_to_scan ?? false,
        profile_id: profileId ?? undefined,
      }),
    onSuccess: (data) => {
      setError(null);
      const rescan = data.rescan;
      if (rescan?.skipped) {
        setRescanNote(
          rescan.reason === 'profile missing ADO credentials'
            ? 'Phases saved. Activate a profile with ADO credentials to re-scan assignments.'
            : 'Phases saved.',
        );
      } else if (rescan?.status === 'started' || rescan?.status === 'already_running') {
        const pid = rescan.profile_id ?? profileId ?? settings?.active_profile_id ?? null;
        if (pid) setBgScanProfileId(pid);
        setRescanNote('Phases saved. Re-scanning repositories in the background…');
      } else if (rescan?.repos_scanned != null) {
        setRescanNote(
          `Phases saved and profile re-scanned (${rescan.repos_scanned} repos re-assigned).`,
        );
      } else {
        setRescanNote('Phases saved.');
      }
      qc.invalidateQueries({ queryKey: ['phases'] });
      qc.invalidateQueries({ queryKey: ['settings'] });
    },
    onError: (e) => {
      setRescanNote(null);
      setBgScanProfileId(null);
      setError(String(e));
    },
  });

  const coverage = payload?.coverage;
  const bandPreview = useMemo(() => {
    const sorted = [...rows].sort((a, b) => a.order - b.order);
    let prev = 0;
    return sorted.map((p) => {
      const band = { ...p, risk_min: prev, risk_max: Number(p.risk_max) };
      prev = Number(p.risk_max);
      return band;
    });
  }, [rows]);

  const updateRow = (id: string, patch: Partial<EditablePhase>) => {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  };

  const addPhase = () => {
    const order = rows.length ? Math.max(...rows.map((r) => r.order)) + 1 : 0;
    const prevMax = rows.length
      ? Math.max(...rows.map((r) => Number(r.risk_max)))
      : 0;
    const riskMax = Math.min(100, Math.round(prevMax + (100 - prevMax) / 2));
    const slug = `phase_${order + 1}`;
    setRows((prev) => [
      ...prev,
      {
        id: slug,
        name: `Phase ${order + 1}`,
        risk_max: riskMax || 100,
        repo_cap: 9999,
        order,
        repo_count: 0,
      },
    ]);
  };

  const requestRemove = (phase: EditablePhase) => {
    if (rows.length <= 1) {
      setError('At least one migration phase is required.');
      return;
    }
    const count = payload?.repo_counts_by_phase[phase.id] ?? 0;
    if (count > 0) {
      setPendingRemoval({ ...phase, repo_count: count });
      return;
    }
    setRows((prev) => prev.filter((r) => r.id !== phase.id));
  };

  const confirmRemoval = (targetId: string) => {
    if (!pendingRemoval) return;
    setRemovals((prev) => [...prev, { phase_id: pendingRemoval.id, move_repos_to: targetId }]);
    setRows((prev) => prev.filter((r) => r.id !== pendingRemoval.id));
    setPendingRemoval(null);
  };

  const requestSave = (spanToScan: boolean) => {
    setRescanNote(null);
    setError(null);
    setPendingSave({ span_to_scan: spanToScan });
  };

  const confirmSave = () => {
    if (!pendingSave) return;
    const body = pendingSave;
    setPendingSave(null);
    saveMut.mutate(body);
  };

  const backgroundScanning = !!bgScanProfileId || scanStatus?.running;

  const activeProfile = settings?.migration_profiles?.find(
    (p) => p.id === (profileId ?? settings?.active_profile_id),
  );

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  return (
    <div className="phase-configurator">
      <p style={{ fontSize: 13, color: '#aaa', marginBottom: 12 }}>
        Phases control how discovery assigns repos by risk score. The last phase must reach 100 so
        the full score range is covered.
        {coverage?.scan_score_min != null && (
          <>
            {' '}
            Current scan scores: {coverage.scan_score_min}–{coverage.scan_score_max}.
          </>
        )}
      </p>

      {coverage?.gaps && coverage.gaps.length > 0 && (
        <div className="phase-coverage-warn">
          {coverage.gaps.map((g) => (
            <p key={g}>{g}</p>
          ))}
        </div>
      )}

      <div className="phase-band-bar">
        {bandPreview.map((p) => (
          <div
            key={p.id}
            className="phase-band-segment"
            style={{ flex: Math.max(1, p.risk_max - (p.risk_min ?? 0)) }}
            title={`${p.name}: ${p.risk_min ?? 0}–${p.risk_max}`}
          >
            <span>{p.name}</span>
          </div>
        ))}
      </div>

      <table className="run-detail-table phase-config-table">
        <thead>
          <tr>
            <th>Order</th>
            <th>Display name</th>
            <th>Id</th>
            <th>Risk ≤</th>
            <th>Repo cap</th>
            <th>Assigned</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows
            .sort((a, b) => a.order - b.order)
            .map((p, idx) => (
              <tr key={p.id}>
                <td>{idx + 1}</td>
                <td>
                  <input
                    className="oai-input"
                    value={p.name}
                    onChange={(e) => updateRow(p.id, { name: e.target.value })}
                  />
                </td>
                <td>
                  <code>{p.id}</code>
                </td>
                <td>
                  <input
                    className="oai-input"
                    type="number"
                    min={1}
                    max={100}
                    step={0.1}
                    value={p.risk_max}
                    onChange={(e) => updateRow(p.id, { risk_max: Number(e.target.value) })}
                  />
                </td>
                <td>
                  <input
                    className="oai-input"
                    type="number"
                    min={1}
                    value={p.repo_cap}
                    onChange={(e) => updateRow(p.id, { repo_cap: Number(e.target.value) })}
                  />
                </td>
                <td>{payload?.repo_counts_by_phase[p.id] ?? 0}</td>
                <td>
                  <button
                    type="button"
                    className="oai-button oai-button-secondary"
                    style={{ padding: '4px 10px', fontSize: 11 }}
                    disabled={rows.length <= 1}
                    onClick={() => requestRemove(p)}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
        </tbody>
      </table>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 16 }}>
        <button type="button" className="oai-button oai-button-secondary" onClick={addPhase}>
          Add phase
        </button>
        <button
          type="button"
          className="oai-button oai-button-secondary"
          disabled={saveMut.isPending || backgroundScanning}
          onClick={() => requestSave(true)}
        >
          Span bands to scan results
        </button>
        <button
          type="button"
          className="oai-button oai-button-primary"
          disabled={saveMut.isPending || backgroundScanning}
          onClick={() => requestSave(false)}
        >
          {saveMut.isPending ? 'Saving…' : backgroundScanning ? 'Re-scanning…' : 'Save phases'}
        </button>
      </div>

      <p style={{ fontSize: 12, color: '#888', marginTop: 12 }}>
        Saving phases re-scans the active profile and re-assigns repos using the updated risk bands
        and repo caps (read-only on ADO).
      </p>

      {settings?.advanced.default_phase && (
        <p style={{ fontSize: 12, color: '#888', marginTop: 12 }}>
          Default migrate phase: <code>{settings.advanced.default_phase}</code> (set in accelerator
          defaults below)
        </p>
      )}

      {error && <p className="badge-manual" style={{ marginTop: 12 }}>{error}</p>}
      {backgroundScanning && !error && (
        <p className="scan-status-banner" style={{ marginTop: 12 }}>
          Re-scanning repositories in the background… Discovery assignments will update when complete.
        </p>
      )}
      {rescanNote && !error && !backgroundScanning && (
        <p className="discovery-save-ok">{rescanNote}</p>
      )}

      {pendingRemoval && (
        <RemovalModal
          phase={pendingRemoval}
          targets={rows.filter((r) => r.id !== pendingRemoval.id)}
          onConfirm={confirmRemoval}
          onCancel={() => setPendingRemoval(null)}
        />
      )}

      {pendingSave && (
        <RescanConfirmModal
          profileName={activeProfile?.name ?? ''}
          spanToScan={pendingSave.span_to_scan}
          onConfirm={confirmSave}
          onCancel={() => setPendingSave(null)}
        />
      )}
    </div>
  );
}
