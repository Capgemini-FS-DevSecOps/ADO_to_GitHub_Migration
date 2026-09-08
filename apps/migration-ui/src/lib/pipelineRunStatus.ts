import type { StepStatus } from './types';

/** Map accelerator pipeline run status to a UI step badge status. */
export function mapPipelineRunStatus(status: string): StepStatus {
  if (status === 'running') return 'running';
  if (status === 'completed') return 'completed';
  if (status === 'dry_run_complete') return 'dry_run_complete';
  if (status === 'failed') return 'failed';
  if (status === 'cancelled') return 'skipped';
  if (status === 'awaiting_approval') return 'awaiting_approval';
  return 'pending';
}

/** Human-readable label for pipeline run status badges. */
export function pipelineRunStatusLabel(status: StepStatus | string): string {
  if (status === 'awaiting_approval') return 'needs approval';
  if (status === 'dry_run_complete') return 'dry run';
  return String(status).replace(/_/g, ' ');
}

export function pipelineRunNeedsApproval(status: string): boolean {
  return status === 'awaiting_approval';
}

/**
 * Normalise the gate override justification sent with a pipeline run.
 *
 * A live run forces past the previous phase's migration gate, and the
 * accelerator only allows that escalation with a written justification, which it
 * records on the gate override. A dry run never reaches the gate, so it carries
 * no justification and cannot leave an override record behind.
 */
export function gateOverrideReason(dryRun: boolean, reason: string): string {
  return dryRun ? '' : reason.trim();
}
