'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import {
  fetchConnectivity,
  testConnectivity,
  updateConnectivity,
  type ConnectivityProfile,
} from '@/lib/llmSettings';
import { fetchSession } from '@/lib/auth';
import { modelsAccessDeniedMessage, modelsPageAllowed } from '@/lib/permissions';

export default function ConnectivitySettingsPage() {
  const qc = useQueryClient();
  const { data: session } = useQuery({ queryKey: ['session'], queryFn: fetchSession });
  const allowed = modelsPageAllowed(session?.permissions);

  const { data, isLoading } = useQuery({
    queryKey: ['connectivity-settings'],
    queryFn: fetchConnectivity,
    enabled: allowed,
  });

  const [proxyEnabled, setProxyEnabled] = useState(false);
  const [proxyHost, setProxyHost] = useState('');
  const [proxyPort, setProxyPort] = useState(8080);
  const [proxyUsername, setProxyUsername] = useState('');
  const [proxyPassword, setProxyPassword] = useState('');
  const [customCaPem, setCustomCaPem] = useState('');
  const [allowCustomModelId, setAllowCustomModelId] = useState(false);
  const [initialized, setInitialized] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [testResult, setTestResult] = useState('');

  useEffect(() => {
    if (data && !initialized) {
      setProxyEnabled(data.proxy_enabled);
      setProxyHost(data.proxy_host);
      setProxyPort(data.proxy_port);
      setProxyUsername(data.proxy_username);
      setAllowCustomModelId(data.allow_custom_model_id);
      setInitialized(true);
    }
  }, [data, initialized]);

  const saveMut = useMutation({
    mutationFn: () =>
      updateConnectivity({
        proxy_enabled: proxyEnabled,
        proxy_host: proxyHost,
        proxy_port: proxyPort,
        proxy_username: proxyUsername,
        proxy_password: proxyPassword || '***',
        custom_ca_pem: customCaPem || (data?.custom_ca_configured ? '***' : ''),
        allow_custom_model_id: allowCustomModelId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['connectivity-settings', 'connectivity-readonly'] });
      setNotice('Re-validate models before enabling — connectivity changes reset validation status.');
      setError('');
      setProxyPassword('');
      setCustomCaPem('');
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Save failed'),
  });

  const testMut = useMutation({
    mutationFn: testConnectivity,
    onSuccess: (result) => {
      setTestResult(`${result.status}: ${result.message}`);
    },
    onError: (e) => setTestResult(e instanceof Error ? e.message : 'Test failed'),
  });

  if (!allowed) {
    return <p className="form-hint">{modelsAccessDeniedMessage()}</p>;
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
      <h2 className="oai-subsection-title">Connectivity</h2>
      <p className="form-hint">
        Environment-level proxy, TLS trust, and optional manual model ID override for all cloud catalog and validation traffic.
      </p>
      {notice && <p className="form-hint">{notice}</p>}
      {error && <p className="oai-error">{error}</p>}
      {testResult && <p className="form-hint">{testResult}</p>}
      <div className="oai-card">
        <h3 className="oai-subsection-title">Corporate proxy</h3>
        <label className="form-hint">
          <input type="checkbox" checked={proxyEnabled} onChange={(e) => setProxyEnabled(e.target.checked)} />{' '}
          Enable HTTP/HTTPS proxy
        </label>
        <div className="form-grid" style={{ marginTop: 12 }}>
          <input
            className="oai-input"
            placeholder="Proxy host"
            value={proxyHost}
            onChange={(e) => setProxyHost(e.target.value)}
          />
          <input
            className="oai-input"
            type="number"
            placeholder="Port"
            value={proxyPort}
            onChange={(e) => setProxyPort(Number(e.target.value))}
          />
          <input
            className="oai-input"
            placeholder="Proxy username (optional)"
            value={proxyUsername}
            onChange={(e) => setProxyUsername(e.target.value)}
          />
          <input
            className="oai-input"
            type="password"
            placeholder={data?.proxy_password === '***' ? 'Password saved (enter to replace)' : 'Proxy password'}
            value={proxyPassword}
            onChange={(e) => setProxyPassword(e.target.value)}
          />
        </div>
      </div>
      <div className="oai-card" style={{ marginTop: 16 }}>
        <h3 className="oai-subsection-title">Custom CA certificate</h3>
        <p className="form-hint">
          {data?.custom_ca_configured
            ? 'A custom CA is configured. Paste new PEM to replace, or leave blank to keep existing.'
            : 'Paste corporate inspection CA PEM for TLS trust.'}
        </p>
        <textarea
          className="oai-input"
          rows={6}
          placeholder="-----BEGIN CERTIFICATE-----"
          value={customCaPem}
          onChange={(e) => setCustomCaPem(e.target.value)}
        />
      </div>
      <div className="oai-card" style={{ marginTop: 16 }}>
        <h3 className="oai-subsection-title">Model override</h3>
        <label className="form-hint">
          <input
            type="checkbox"
            checked={allowCustomModelId}
            onChange={(e) => setAllowCustomModelId(e.target.checked)}
          />{' '}
          Allow custom model ID when catalog/discovery does not list the desired model
        </label>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <button
          type="button"
          className="oai-button oai-button-primary"
          disabled={saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          Save connectivity
        </button>
        <button
          type="button"
          className="oai-button"
          disabled={testMut.isPending}
          onClick={() => testMut.mutate()}
        >
          Test connection
        </button>
      </div>
    </div>
  );
}
