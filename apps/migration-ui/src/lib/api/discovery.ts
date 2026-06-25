/** TypeScript types for the discovery API (feature 008, US3). */

export interface DiscoveryResultItem {
  id: string;
  organization_id: string;
  repository_id: string;
  repository_name: string;
  pipeline_count: number;
  last_scanned_at: string;
  scan_status: string;
  metadata: Record<string, unknown>;
}

export interface DiscoveryResultsResponse {
  results: DiscoveryResultItem[];
  total_count: number;
  scan_timestamp: string;
}

export interface ScanRequest {
  organizations?: string[];
  force_refresh?: boolean;
}

export interface ScanResponse {
  scan_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  organizations_scanned: number | null;
  repositories_discovered: number | null;
}

export interface ScanOrgResult {
  organization_id: string;
  status: 'completed' | 'failed';
  repositories_discovered: number;
  error: string | null;
}

export interface ScanStatusResponse {
  scan_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  organizations: ScanOrgResult[];
}

export interface ScanSummary {
  total_repositories: number;
  by_status: Record<string, number>;
  by_organization: Record<string, number>;
}
