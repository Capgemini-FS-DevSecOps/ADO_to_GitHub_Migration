'use client';

import type { PipelineStep, StepStatus } from '@/lib/types';
import { pipelineRunStatusLabel } from '@/lib/pipelineRunStatus';

function StepIcon({ status }: { status: StepStatus }) {
  if (status === 'completed' || status === 'dry_run_complete') {
    return (
      <svg className="pipeline-step-icon pipeline-step-icon-done" viewBox="0 0 24 24" width="100%" height="100%">
        <circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M7 12.5l3.5 3.5L17 8.5" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (status === 'warn') {
    return (
      <svg className="pipeline-step-icon pipeline-step-icon-warn" viewBox="0 0 24 24" width="100%" height="100%">
        <circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M12 7v6" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="12" cy="16.5" r="1.2" fill="currentColor" />
      </svg>
    );
  }
  if (status === 'failed') {
    return (
      <svg className="pipeline-step-icon pipeline-step-icon-failed" viewBox="0 0 24 24" width="100%" height="100%">
        <circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M8 8l8 8M16 8l-8 8" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    );
  }
  if (status === 'running') {
    return (
      <svg className="pipeline-step-icon pipeline-step-icon-running" viewBox="0 0 24 24" width="100%" height="100%">
        <circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="50 20" strokeLinecap="round" />
      </svg>
    );
  }
  if (status === 'skipped') {
    return (
      <svg className="pipeline-step-icon pipeline-step-icon-skipped" viewBox="0 0 24 24" width="100%" height="100%">
        <circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M8 12h8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  }
  // pending or awaiting_approval
  return (
    <svg className="pipeline-step-icon pipeline-step-icon-pending" viewBox="0 0 24 24" width="100%" height="100%">
      <circle cx="12" cy="12" r="11" fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function stepColor(status: StepStatus): string {
  switch (status) {
    case 'completed':
    case 'dry_run_complete':
      return '#22c55e';
    case 'warn':
      return '#f59e0b';
    case 'failed':
      return '#ef4444';
    case 'running':
      return '#35B8FF';
    case 'skipped':
      return '#666';
    case 'awaiting_approval':
      return '#ffc107';
    default:
      return 'rgba(53, 184, 255, 0.3)';
  }
}

export function StepPipelineBar({ steps, compact = false }: { steps: PipelineStep[]; compact?: boolean }) {
  if (!steps.length) return null;

  const iconSize = compact ? 20 : 28;

  return (
    <div className={`pipeline-bar${compact ? ' pipeline-bar-compact' : ''}`}>
      <div className="pipeline-steps-row">
        {steps.map((step, i) => {
          const color = stepColor(step.status as StepStatus);
          const nextStep = steps[i + 1];
          const connectorColor = nextStep
            ? stepColor(nextStep.status as StepStatus)
            : color;
          return (
            <div key={step.id} className="pipeline-step-node">
              <div
                className="pipeline-step-icon-wrap"
                style={{ width: iconSize, height: iconSize, color }}
                title={`${step.label}: ${step.status}`}
              >
                <StepIcon status={step.status as StepStatus} />
              </div>
              {i < steps.length - 1 && (
                <div
                  className="pipeline-step-connector"
                  style={{ backgroundColor: connectorColor }}
                  aria-hidden
                />
              )}
            </div>
          );
        })}
      </div>
      {!compact && (
        <div className="pipeline-labels-row">
          {steps.map((step) => (
            <span
              key={`label-${step.id}`}
              className="pipeline-label"
              style={{ color: stepColor(step.status as StepStatus) }}
            >
              {step.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function StepStatusBadge({ status }: { status: StepStatus | string }) {
  const label = pipelineRunStatusLabel(status);
  const css =
    status === 'dry_run_complete'
      ? 'dry_run'
      : status === 'awaiting_approval'
        ? 'awaiting_approval'
        : status;
  return <span className={`step-badge step-badge-${css}`}>{label}</span>;
}
