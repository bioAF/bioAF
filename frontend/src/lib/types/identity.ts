// Users, roles, permissions and the audit log.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

import type { components } from "../../../contracts/internal-api";

export interface User {
  id: number;
  email: string;
  name: string | null;
  role_id: number;
  role_name: string;
  status: "active" | "invited" | "deactivated";
  organization_id: number;
  last_login: string | null;
  session_credentials_configured: boolean;
  created_at: string;
  updated_at: string;
}

export interface PermissionEntry {
  resource: string;
  action: string;
}

export interface Role {
  id: number;
  name: string;
  description: string | null;
  organization_id: number;
  is_system: boolean;
  permissions: PermissionEntry[];
  created_at: string | null;
}

export interface RoleListResponse {
  roles: Role[];
  total: number;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

/** A user as other records reference them. Generated transport (internal schema). */
export type UserSummary = components["schemas"]["UserSummary"];

export interface AuditLogEntry {
  id: number;
  timestamp: string;
  user: UserSummary | null;
  entity_type: string;
  entity_id: number;
  action: string;
  details: Record<string, unknown> | null;
  previous_value: Record<string, unknown> | null;
}

export interface AuditLogResponse {
  entries: AuditLogEntry[];
  total: number;
  page: number;
  page_size: number;
}
