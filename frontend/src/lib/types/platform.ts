// Platform status, navigation and search.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

export interface BootstrapStatus {
  setup_complete: boolean;
  smtp_configured?: boolean; // only present for authenticated callers
  has_setup_code: boolean;
  has_admin: boolean;
  // Active BAL provider capabilities; only present for authenticated callers.
  // See useCapabilities() and the backend ProviderCapabilities model.
  capabilities?: Partial<Record<string, boolean>>;
}

export interface ComponentState {
  key: string;
  name: string;
  description: string;
  category: string;
  enabled: boolean;
  status: string;
  config: Record<string, unknown>;
  dependencies: string[];
  estimated_monthly_cost: string;
  updated_at: string | null;
}

export interface TerraformRun {
  id: number;
  triggered_by_user_id: number;
  action: string;
  component_key: string | null;
  plan_summary: {
    add: Array<{ type: string; name: string; address: string }>;
    change: Array<{ type: string; name: string; address: string }>;
    destroy: Array<{ type: string; name: string; address: string }>;
    add_count: number;
    change_count: number;
    destroy_count: number;
  } | null;
  status: string;
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
}

export interface HealthStatus {
  status: "healthy" | "degraded" | "unhealthy";
  services: Record<string, { status: string; error?: string }>;
}

export interface NavItem {
  label: string;
  href: string;
  icon: string;
  active: boolean;
  phase?: string;
  adminOnly?: boolean;
}

export interface SearchHit {
  entity_type: string;
  entity_id: number;
  title: string;
  snippet: string | null;
  /** Server-computed in-app destination for this hit. */
  url: string;
  experiment_id: number | null;
  relevance_score: number | null;
}

export interface SearchResult {
  results: SearchHit[];
  total: number;
  page: number;
  page_size: number;
  /** Per-type match counts for the searched types, for the type filter. */
  type_counts: Record<string, number>;
}
