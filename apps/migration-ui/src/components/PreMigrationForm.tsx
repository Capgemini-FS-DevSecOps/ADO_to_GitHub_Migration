'use client';

import { useState } from 'react';
import { DependencyGraph } from '@/components/DependencyGraph';

export interface PreMigrationFormData {
  form_id: string;
  repository_id: string;
  required_fields: Record<string, { type: string; description: string }>;
  optional_fields: Record<string, { type: string; description: string }>;
  dependency_graph: {
    nodes: string[];
    edges: { source: string; target: string }[];
  };
}

interface PreMigrationFormProps {
  form: PreMigrationFormData;
  onSubmit: (values: Record<string, unknown>) => void;
  onCancel: () => void;
  busy?: boolean;
  validationErrors?: Record<string, string>;
}

export function PreMigrationForm({
  form,
  onSubmit,
  onCancel,
  busy,
  validationErrors,
}: PreMigrationFormProps) {
  const [values, setValues] = useState<Record<string, unknown>>({});

  const handleChange = (name: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = () => {
    onSubmit(values);
  };

  const renderField = (
    name: string,
    def: { type: string; description: string },
    required: boolean,
  ) => {
    const error = validationErrors?.[name];
    return (
      <div className="form-row" key={name}>
        <label>
          {name}
          {required ? <span className="form-required">*</span> : null}
          <span className="form-hint" style={{ display: 'block', fontSize: 11 }}>
            {def.description}
          </span>
        </label>
        {def.type === 'text' || def.type === 'string' ? (
          <input
            className="oai-input"
            value={String(values[name] ?? '')}
            onChange={(e) => handleChange(name, e.target.value)}
          />
        ) : def.type === 'textarea' ? (
          <textarea
            className="oai-input"
            rows={3}
            value={String(values[name] ?? '')}
            onChange={(e) => handleChange(name, e.target.value)}
          />
        ) : def.type === 'json' ? (
          <textarea
            className="oai-input"
            rows={6}
            placeholder='{"key": "value"}'
            value={String(values[name] ?? '')}
            onChange={(e) => handleChange(name, e.target.value)}
          />
        ) : def.type === 'checkbox' ? (
          <input
            type="checkbox"
            checked={Boolean(values[name])}
            onChange={(e) => handleChange(name, e.target.checked)}
          />
        ) : (
          <input
            className="oai-input"
            value={String(values[name] ?? '')}
            onChange={(e) => handleChange(name, e.target.value)}
          />
        )}
        {error && <p className="badge-manual" style={{ marginTop: 4 }}>{error}</p>}
      </div>
    );
  };

  return (
    <div className="oai-card pre-migration-form">
      <h2 className="oai-subsection-title">Pre-Migration Form</h2>
      <p style={{ fontSize: 13, color: '#aaa', marginBottom: 16 }}>
        Repository: <code>{form.repository_id}</code>
      </p>

      {form.dependency_graph.nodes.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <h3 style={{ fontSize: 14, marginBottom: 8 }}>Dependency Graph</h3>
          <DependencyGraph
            nodes={form.dependency_graph.nodes}
            edges={form.dependency_graph.edges}
          />
        </div>
      )}

      <div className="form-grid">
        {Object.entries(form.required_fields).map(([name, def]) =>
          renderField(name, def, true),
        )}
        {Object.entries(form.optional_fields).map(([name, def]) =>
          renderField(name, def, false),
        )}
      </div>

      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <button
          type="button"
          className="oai-button oai-button-primary"
          disabled={busy}
          onClick={handleSubmit}
        >
          {busy ? 'Submitting…' : 'Submit form'}
        </button>
        <button
          type="button"
          className="oai-button oai-button-secondary"
          onClick={onCancel}
          disabled={busy}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
