// Client for the infrastructure-update endpoints. The check re-plans deployed
// modules (after re-aligning naming to the live deployment) and reports the
// pending changes split into additive (create/update) and destructive
// (delete/replace). The apply only ever applies additive resources.

import { api } from "./api";

export interface ResourceInfo {
  address: string;
  type: string;
  action: string;
  description: string;
}

export interface DestructiveResourceInfo extends ResourceInfo {
  stateful: boolean;
}

export interface ModuleUpdateInfo {
  module: string;
  add_count: number;
  change_count: number;
  destroy_count: number;
  has_changes: boolean;
}

export interface CheckUpdatesResult {
  has_changes: boolean;
  has_additive: boolean;
  has_destructive: boolean;
  requires_approval: boolean;
  // true when the backend already started applying the additive changes.
  applying: boolean;
  realigned: { org_slug?: string; stack_uid?: string } | null;
  modules_with_additive: string[];
  modules: ModuleUpdateInfo[];
  additive_resources: ResourceInfo[];
  destructive_resources: DestructiveResourceInfo[];
}

export const infrastructure = {
  // Re-plan deployed modules. Additive-only plans begin applying in the
  // background (applying=true); a plan that also contains destructive changes
  // is returned without applying (the caller can apply the additive subset).
  checkUpdates: () =>
    api.post<CheckUpdatesResult>("/api/v1/infrastructure/stack/check-updates"),
  // Apply the named modules, additive-only (create/update resources only).
  applyUpdates: (modules: string[]) =>
    api.post<{ applying: boolean; modules: string[] }>(
      "/api/v1/infrastructure/stack/apply-updates",
      { modules },
    ),
};
