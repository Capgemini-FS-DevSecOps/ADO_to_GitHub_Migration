'use client';

import type { PipelineStep, StepStatus } from '@/lib/types';

const STATUS_COLOR: Record<StepStatus, string> = {
  pending: 'rgba(53, 184, 255, 0.2)',
  running: '#35B8FF',
  completed: '#00ff88',
  failed: '#FF6B6B',
  skipped: '#666',
};

export function StepPipelineBar({ steps, compact = false }: { steps: PipelineStep[]; compact?: boolean }) {
  if (!steps.length) return null;

  return (
    <div className={`pipeline-bar${compact ? ' pipeline-bar-compact' : ''}`}>
      <div className="pipeline-segments-row">
        {steps.map((step, i) => (
          <div key={step.id} className="pipeline-segment-cell">
            <div
              className="pipeline-segment"
              style={{ backgroundColor: STATUS_COLOR[step.status as StepStatus] || STATUS_COLOR.pending }}
              title={`${step.label}: ${step.status}`}
            />
            {i < steps.length - 1 && <div className="pipeline-connector" aria-hidden />}
          </div>
        ))}
      </div>
      {!compact && (
        <div className="pipeline-labels-row">
          {steps.map((step) => (
            <span
              key={`label-${step.id}`}
              className={`pipeline-label pipeline-label-${step.status}`}
            >
              {step.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function StepStatusBadge({ status }: { status: StepStatus }) {
  return <span className={`step-badge step-badge-${status}`}>{status}</span>;
}
