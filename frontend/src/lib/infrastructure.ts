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

/** The confirmed update was accepted as one operation; progress and outcome are read from it. */
export interface UpdateCheckAccepted {
  message: string;
  operation_id: number;
}

export const infrastructure = {
  // Start the confirmed "Check for infrastructure updates" action (mockup M1): one operation
  // plans every deployed module and applies the additive changes automatically.
  checkUpdates: (idempotencyKey?: string) =>
    api.post<UpdateCheckAccepted>("/api/v1/infrastructure/stack/check-updates", undefined, { idempotencyKey }),
  // Apply the named modules, additive-only (create/update resources only).
  applyUpdates: (modules: string[]) =>
    api.post<{ applying: boolean; modules: string[]; operation_id: number }>(
      "/api/v1/infrastructure/stack/apply-updates",
      { modules },
    ),
};
