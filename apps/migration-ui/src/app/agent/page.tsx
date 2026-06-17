'use client';

import { AgentChat } from '@/components/AgentChat';

export default function AgentPage() {
  return (
    <div>
      <h1 className="oai-page-title">Migration Agent</h1>
      <p className="form-hint">
        Planner → executor → validator (PEV) sessions default to dry-run. Operators may request live
        execution; platform admin or approver must approve on the unified live queue before irreversible steps run.
      </p>
      <div className="oai-card agent-page-card">
        <AgentChat />
      </div>
    </div>
  );
}
