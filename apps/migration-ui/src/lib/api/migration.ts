/** TypeScript types for the migration API (feature 008, US4/US5). */

export interface OnDemandMigrationRequest {
  repository_id: string;
  organization_id: string;
  pre_migration_form_id: string;
  dry_run?: boolean;
}

export interface OnDemandMigrationResponse {
  operation_id: string;
  status: string;
  repository_id: string;
  operation_type: string;
  created_at: string;
}

export interface CreateWaveRequest {
  name: string;
  description?: string;
  repository_ids: string[];
  organization_id: string;
}

export interface CreateWaveResponse {
  wave_id: string;
  name: string;
  description: string | null;
  status: string;
  repository_count: number;
  created_at: string;
}

export interface ExecuteWaveRequest {
  dry_run?: boolean;
}

export interface ExecuteWaveResponse {
  wave_id: string;
  status: string;
  started_at: string;
  repositories_in_wave: number;
}

export interface WaveRepositoryItem {
  repository_id: string;
  migration_order: number;
  status: string;
}

export interface WaveStatusResponse {
  wave_id: string;
  name: string;
  status: string;
  created_at: string;
  started_at: string | null;
  repositories: WaveRepositoryItem[];
}

export interface PreMigrationFormRequest {
  repository_id: string;
  organization_id: string;
}

export interface PreMigrationFormResponse {
  form_id: string;
  repository_id: string;
  required_fields: Record<string, { type: string; description: string }>;
  optional_fields: Record<string, { type: string; description: string }>;
  dependency_graph: {
    nodes: string[];
    edges: { source: string; target: string }[];
  };
}

export interface PreMigrationFormSubmit {
  target_github_org: string;
  team_mapping: Record<string, unknown>;
  pipeline_config: Record<string, unknown>;
  repo_description?: string;
  topics?: string[];
  labels?: string[];
}

export interface PreMigrationFormSubmitResponse {
  form_id: string;
  form_status: string;
  validated_at: string;
}
