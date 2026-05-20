// Client for the infrastructure-update endpoints (re-plan deployed modules,
// apply additive changes, gate stateful destroy/replace behind approval).

import { api } from "./api";

export interface StatefulResourceInfo {
  address: string;
  type: string;
  action: string;
  description: string;
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
  requires_approval: boolean;
  applying: boolean;
  modules_with_changes: string[];
  modules: ModuleUpdateInfo[];
  destructive_resources: StatefulResourceInfo[];
}

export const infrastructure = {
  // Re-plan deployed modules. Additive-only changes begin applying in the
  // background (applying=true); a stateful destroy/replace is returned with
  // requires_approval=true and is not applied.
  checkUpdates: () =>
    api.post<CheckUpdatesResult>("/api/v1/infrastructure/stack/check-updates"),
  // Apply named modules after the user approves a destructive update.
  applyUpdates: (modules: string[]) =>
    api.post<{ applying: boolean; modules: string[] }>(
      "/api/v1/infrastructure/stack/apply-updates",
      { modules },
    ),
};
