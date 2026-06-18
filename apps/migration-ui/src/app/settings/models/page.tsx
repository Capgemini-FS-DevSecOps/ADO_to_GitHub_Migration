'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import {
  canEnableModel,
  deleteModel,
  fetchCatalog,
  fetchConnectivity,
  saveModel,
  validateModel,
  validationBadgeLabel,
  type CatalogEntry,
  type ValidationResult,
} from '@/lib/llmSettings';
import { fetchSession } from '@/lib/auth';
import { modelsAccessDeniedMessage, modelsPageAllowed } from '@/lib/permissions';
import { ACCEL } from '@/lib/api';

function isLikelyOllamaUrl(url: string): boolean {
  const trimmed = url.trim();
  if (!trimmed) return false;
  try {
    const parsed = new URL(trimmed.includes('://') ? trimmed : `http://${trimmed}`);
    return Boolean(parsed.hostname);
  } catch {
    return false;
  }
}

async function listModels() {
  const r = await fetch(`${ACCEL}/v1/settings/llm-models`, { credentials: 'include' });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export default function LlmModelsPage() {
  const qc = useQueryClient();
  const { data: session } = useQuery({ queryKey: ['session'], queryFn: fetchSession });
  const allowed = modelsPageAllowed(session?.permissions);

  const { data, isLoading } = useQuery({
    queryKey: ['llm-models-admin'],
    queryFn: listModels,
    enabled: allowed,
  });

  const { data: connectivity } = useQuery({
    queryKey: ['connectivity-readonly'],
    queryFn: fetchConnectivity,
    enabled: allowed,
  });

  const [displayName, setDisplayName] = useState('');
  const [provider, setProvider] = useState('openai');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [catalogEntries, setCatalogEntries] = useState<CatalogEntry[]>([]);
  const [catalogStale, setCatalogStale] = useState(false);
  const [catalogSource, setCatalogSource] = useState('preset');
  const [selectedModelId, setSelectedModelId] = useState('');
  const [customModelId, setCustomModelId] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [discoveryError, setDiscoveryError] = useState('');
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState('');

  const selectedEntry = useMemo(
    () => catalogEntries.find((entry) => entry.id === selectedModelId),
    [catalogEntries, selectedModelId],
  );

  const showOverride = Boolean(
    connectivity?.allow_custom_model_id && (catalogEntries.length === 0 || customModelId),
  );

  const effectiveModelId = showOverride && customModelId ? customModelId : selectedModelId;

  const canLoadCatalog =
    provider === 'stub' ||
    (provider === 'ollama' && isLikelyOllamaUrl(baseUrl)) ||
    ((provider === 'openai' || provider === 'anthropic') && Boolean(apiKey.trim()));

  const canValidate =
    provider === 'stub' || Boolean(effectiveModelId) || canLoadCatalog;

  useEffect(() => {
    setValidation(null);
    setError('');
    setDiscoveryError('');
    setCatalogStale(false);
    setCustomModelId('');
    if (provider === 'stub') {
      setCatalogEntries([{ id: 'stub', display_name: 'Stub', provider: 'stub', source: 'preset' }]);
      setSelectedModelId('stub');
      return;
    }
    if (provider === 'ollama') {
      setBaseUrl((prev) => prev || 'http://localhost:11434');
    }
    setCatalogEntries([]);
    setSelectedModelId('');
  }, [provider, apiKey, baseUrl]);

  async function loadCatalog(): Promise<CatalogEntry[]> {
    if (!canLoadCatalog || catalogLoading) return catalogEntries;
    if (provider === 'stub') return catalogEntries;

    setCatalogLoading(true);
    setError('');
    setDiscoveryError('');
    setValidation(null);
    try {
      const catalog = await fetchCatalog({
        provider,
        apiKey: apiKey || undefined,
        baseUrl: provider === 'ollama' ? baseUrl.trim() : undefined,
      });
      setCatalogEntries(catalog.entries);
      setCatalogStale(catalog.stale);
      setCatalogSource(catalog.source);
      setDiscoveryError(catalog.discovery_error ?? '');
      setSelectedModelId((prev) => {
        if (prev && catalog.entries.some((entry) => entry.id === prev)) {
          return prev;
        }
        return catalog.entries[0]?.id ?? '';
      });
      return catalog.entries;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Catalog load failed');
      setCatalogEntries([]);
      setSelectedModelId('');
      return [];
    } finally {
      setCatalogLoading(false);
    }
  }

  async function resolveModelIdForValidation(): Promise<string> {
    if (effectiveModelId) return effectiveModelId;
    if (provider === 'stub') return 'stub';
    if (!canLoadCatalog) return '';
    const entries = await loadCatalog();
    if (showOverride && customModelId) return customModelId;
    return entries[0]?.id ?? '';
  }

  const createMut = useMutation({
    mutationFn: () =>
      saveModel({
        display_name: displayName,
        provider,
        model_id: effectiveModelId || displayName,
        api_key: apiKey,
        base_url: provider === 'ollama' ? baseUrl : null,
        catalog_source: showOverride && customModelId ? 'override' : catalogSource,
        catalog_label: selectedEntry?.display_name ?? customModelId,
        enabled: validation?.status === 'passed',
        validation_status: validation?.status === 'passed' ? 'passed' : 'never_validated',
        validation_at: validation?.validated_at ?? null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['llm-models-admin', 'llm-models'] });
      setDisplayName('');
      setApiKey('');
      setSelectedModelId('');
      setCustomModelId('');
      setValidation(null);
      setError('');
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Save failed'),
  });

  const deleteMut = useMutation({
    mutationFn: (modelId: string) => deleteModel(modelId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['llm-models-admin'] });
      qc.invalidateQueries({ queryKey: ['llm-models'] });
      setError('');
    },
    onError: (e) => setError(e instanceof Error ? e.message : 'Delete failed'),
  });

  async function handleValidate() {
    if (validating || !canValidate) return;
    setValidating(true);
    setError('');
    try {
      const modelId = await resolveModelIdForValidation();
      if (!modelId) {
        setError(
          provider === 'ollama'
            ? 'No local models found — check the Ollama base URL and that Ollama is running.'
            : 'No models found — check credentials and try Search models.',
        );
        return;
      }
      const result = await validateModel({
        display_name: displayName,
        provider,
        model_id: modelId,
        api_key: apiKey,
        base_url: provider === 'ollama' ? baseUrl : null,
        catalog_source: showOverride && customModelId ? 'override' : catalogSource,
      });
      setValidation(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Validation failed');
    } finally {
      setValidating(false);
    }
  }

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
      <h2 className="oai-subsection-title">LLM models</h2>
      <p className="form-hint">
        Enter provider credentials, search the model catalog, validate connectivity, then save and enable.
        For local Ollama, use Search models or Validate to discover installed models.
      </p>
      <div className="oai-card" style={{ marginBottom: 16 }}>
        {(data?.models ?? []).map(
          (m: {
            id: string;
            display_name: string;
            provider: string;
            catalog_label?: string;
            model_id: string;
            validation_status?: string;
            validation_at?: string | null;
          }) => (
            <div key={m.id} className="credential-meta" style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <p style={{ flex: 1, margin: 0 }}>
                <strong>{m.display_name}</strong> — {m.provider} / {m.catalog_label || m.model_id}{' '}
                <span className="form-hint">
                  ({validationBadgeLabel(m.validation_status)}
                  {m.validation_at ? ` · ${m.validation_at}` : ''})
                </span>
              </p>
              <button
                type="button"
                className="oai-button oai-button-secondary"
                disabled={deleteMut.isPending}
                onClick={() => {
                  if (window.confirm(`Delete model "${m.display_name}"?`)) {
                    deleteMut.mutate(m.id);
                  }
                }}
              >
                Delete
              </button>
            </div>
          ),
        )}
        {!data?.models?.length && (
          <p className="form-hint">No models configured — the agent chat will prompt you to add one.</p>
        )}
      </div>
      <div className="oai-card">
        <h3 className="oai-subsection-title">Add model</h3>
        {error && <p className="oai-error">{error}</p>}
        {discoveryError && <p className="oai-error">{discoveryError}</p>}
        {catalogStale && <p className="form-hint">Catalog may be outdated — live fetch failed; preset list shown.</p>}
        <div className="form-grid">
          <input
            className="oai-input"
            placeholder="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
          <select className="oai-input" value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="ollama">Local / Ollama</option>
            <option value="stub">Stub</option>
          </select>
          {provider !== 'stub' && provider !== 'ollama' && (
            <input
              className="oai-input"
              type="password"
              placeholder="API key (required to load model catalog)"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          )}
          {provider === 'ollama' && (
            <>
              <input
                className="oai-input"
                placeholder="Base URL (http://localhost:11434)"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
              <input
                className="oai-input"
                type="password"
                placeholder="Bearer token (optional — if Ollama requires auth)"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
              <p className="form-hint">
                Ollama runs on your machine. When the API runs in Docker, localhost is rewritten to
                host.docker.internal automatically.
              </p>
            </>
          )}
          <select
            className="oai-input"
            value={selectedModelId}
            onChange={(e) => setSelectedModelId(e.target.value)}
            disabled={catalogLoading || catalogEntries.length === 0}
          >
            {catalogLoading && <option value="">Loading catalog…</option>}
            {!catalogLoading && catalogEntries.length === 0 && (
              <option value="">
                {provider === 'ollama' && !isLikelyOllamaUrl(baseUrl)
                  ? 'Enter base URL, then search for models'
                  : provider === 'ollama'
                    ? 'Search models to load local models'
                    : (provider === 'openai' || provider === 'anthropic') && !apiKey
                      ? 'Enter API key to load models'
                      : 'Search models to load catalog'}
              </option>
            )}
            {catalogEntries.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.display_name}
              </option>
            ))}
          </select>
          {showOverride && (
            <input
              className="oai-input"
              placeholder="Custom model ID override"
              value={customModelId}
              onChange={(e) => setCustomModelId(e.target.value)}
            />
          )}
        </div>
        {validation && (
          <p className={validation.status === 'passed' ? 'form-hint' : 'oai-error'}>
            {validationBadgeLabel(validation.status)} — {validation.message}
          </p>
        )}
        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          <button
            type="button"
            className="oai-button"
            disabled={!canLoadCatalog || catalogLoading || provider === 'stub'}
            onClick={() => void loadCatalog()}
          >
            {catalogLoading ? 'Searching…' : 'Search models'}
          </button>
          <button
            type="button"
            className="oai-button"
            disabled={validating || !canValidate}
            onClick={() => void handleValidate()}
          >
            {validating ? 'Validating…' : 'Validate'}
          </button>
          <button
            type="button"
            className="oai-button oai-button-primary"
            disabled={
              createMut.isPending ||
              !displayName ||
              !effectiveModelId ||
              !canEnableModel(validation?.status)
            }
            onClick={() => createMut.mutate()}
          >
            Save &amp; enable
          </button>
        </div>
      </div>
    </div>
  );
}
