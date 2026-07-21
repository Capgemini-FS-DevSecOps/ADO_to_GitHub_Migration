'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchSettings, updateAdvanced } from '@/lib/api';

export default function AdvancedSettingsPage() {
  const qc = useQueryClient();
  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
  });

  const advMut = useMutation({
    mutationFn: updateAdvanced,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  });

  if (isLoading) {
    return (
      <div className="oai-loading">
        <div className="oai-spinner" />
      </div>
    );
  }

  const adv = settings?.advanced;
  if (!adv) return null;

  return (
    <div style={{ maxWidth: 560 }}>
      <div className="oai-card form-page">
        <h2 className="oai-subsection-title">Accelerator defaults</h2>
        <div className="form-grid">
          {[
            ['config_path', 'text'],
            ['db_path', 'text'],
            ['output_dir', 'text'],
            ['migration_strategy', 'text'],
            ['repo_parallel', 'number'],
            ['pipeline_parallel', 'number'],
          ].map(([key, type]) => (
            <div className="form-row" key={key}>
              <label>{key.replace(/_/g, ' ')}</label>
              <input
                type={type}
                defaultValue={String(adv[key as keyof typeof adv] ?? '')}
                id={`adv-${key}`}
                className="oai-input"
              />
            </div>
          ))}
          <div className="form-row form-row-checkbox">
            <label htmlFor="adv-dry_run_default">
              <input type="checkbox" id="adv-dry_run_default" defaultChecked={adv.dry_run_default} />
              Dry run by default
            </label>
          </div>
        </div>
        <button
          type="button"
          className="oai-button oai-button-primary"
          style={{ marginTop: 16 }}
          onClick={() => {
            const payload: Record<string, unknown> = {
              dry_run_default: (document.getElementById('adv-dry_run_default') as HTMLInputElement)?.checked,
            };
            ['config_path', 'db_path', 'output_dir', 'migration_strategy'].forEach((k) => {
              const el = document.getElementById(`adv-${k}`) as HTMLInputElement;
              if (el?.value) payload[k] = el.value;
            });
            ['repo_parallel', 'pipeline_parallel'].forEach((k) => {
              const el = document.getElementById(`adv-${k}`) as HTMLInputElement;
              if (el?.value) payload[k] = Number(el.value);
            });
            advMut.mutate(payload);
          }}
        >
          Save advanced settings
        </button>
      </div>
    </div>
  );
}
