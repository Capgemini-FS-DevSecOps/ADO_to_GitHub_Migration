'use client';

import { AgentChat } from '@/components/AgentChat';

export default function AgentPage() {
  return (
    <div>
      <h1 className="oai-page-title">Migration Agent</h1>
      <p>
        Chat with the migration agent (placeholder). Select a model and ask about discovery,
        phase planning, or pipeline conversion.
      </p>
      <div className="oai-card agent-page-card">
        <AgentChat />
      </div>
    </div>
  );
}
