/** Agent message types for the streamlined migration agent interface (feature 008).

Defines message types for migration progress displayed as conversational
agent messages rather than pipeline/PEV status indicators.
*/

export type AgentMessageType =
  | 'text'
  | 'migration_progress'
  | 'migration_complete'
  | 'migration_failed'
  | 'discovery_summary'
  | 'dependency_analysis'
  | 'form_request'
  | 'form_response'
  | 'wave_created'
  | 'wave_progress';

export interface AgentMessage {
  id: string;
  role: 'user' | 'assistant';
  type: AgentMessageType;
  content: string;
  timestamp: string;
  metadata?: {
    repository_id?: string;
    operation_id?: string;
    wave_id?: string;
    progress?: {
      current: number;
      total: number;
      repository_id: string;
      status: string;
    };
    discovery?: {
      organizations_scanned: number;
      repositories_discovered: number;
      errors: string[];
    };
    dependency_graph?: {
      nodes: string[];
      edges: { source: string; target: string }[];
      has_cycle: boolean;
    };
    form?: {
      form_id: string;
      required_fields: string[];
      optional_fields: string[];
    };
  };
}

/** T066: Model capabilities metadata from LLM bridge */
export interface ModelCapabilities {
  supports_tool_calling: boolean;
  supports_streaming: boolean;
  supports_thinking: boolean;
  max_context_tokens: number;
}

/** T066: SSE event kinds from LangGraph streaming */
export type SSEEventKind =
  | 'token'
  | 'thinking'
  | 'message'
  | 'tool_call'
  | 'tool_result'
  | 'status'
  | 'progress'
  | 'task_update'
  | 'form_request'
  | 'heartbeat'
  | 'done'
  | '__done__';

/** T066: SSE event structure from agent streaming endpoint */
export interface SSEEvent {
  kind: SSEEventKind;
  content: string;
  subagent: 'orchestrator' | 'planner' | 'executor' | 'validator';
  meta?: {
    name?: string;
    arguments?: Record<string, unknown>;
    title?: string;
    description?: string;
    fields?: Array<{
      name: string;
      label: string;
      type: string;
      required?: boolean;
      options?: Array<{ value: string; label: string }>;
    }>;
  };
  __done__?: boolean;
  reply?: string;
  pending_form?: unknown;
}

/** T071: Session state machine states (FR-066) */
export type SessionState =
  | 'idle'
  | 'thinking'
  | 'planning'
  | 'executing'
  | 'validating'
  | 'awaiting_input'
  | 'awaiting_approval'
  | 'completed'
  | 'failed';

export interface AgentSession {
  id: string;
  messages: AgentMessage[];
  status: SessionState;
  /** T071: PEV cycle iteration counters */
  iteration_count?: number;
  pev_retry_count?: number;
  /** T071: Current PEV cycle number */
  cycle_number?: number;
  /** T071: Dry-run flag */
  dry_run?: boolean;
  /** T066: Model capabilities for current session */
  model_capabilities?: ModelCapabilities;
}
