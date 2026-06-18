'use client';

import { AgentChat } from '@/components/AgentChat';

export default function AgentPage() {
  return (
    <div>
      <h1 className="oai-page-title">Migration Agent</h1>
      <p className="form-hint">
        Chat with the migration assistant, then generate a plan (planner subagent). Execute runs
        connect → migrate → validate via the same pipeline as the Migrate tab (dry-run by default).
        Operators may request live execution; platform admin or approver can run live migrations directly.
      </p>
      <div className="oai-card agent-page-card">
        <AgentChat />
      </div>
    </div>
  );
}
