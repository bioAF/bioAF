"use client";

import { useRef } from "react";
import { usePermissions } from "@/hooks/usePermissions";

/** The infrastructure permissions that gate mutations; reading needs only `view`. */
export type InfrastructureAction = "configure" | "deploy" | "create";

/**
 * Whether the user may start a mutation on `resource`, and if not, why.
 *
 * An action is allowed only once permissions have loaded, the read succeeded, and the role has
 * the action. Controls stay visible: callers disable them and show `reason`, so a role that cannot
 * act still sees what exists. `stillAllowed` reads the latest answer, for a handler that awaited a
 * confirmation. The backend checks the same permissions; this is the interface, not the guard.
 */
export function usePermissionGate<A extends string>(resource: string) {
  const { canAccess, loading, failed } = usePermissions();

  const allowed = (action: A) => !loading && !failed && canAccess(resource, action);

  const reason = (action: A): string | null => {
    if (loading) return "Checking your permissions.";
    if (failed) return "Your permissions could not be confirmed, so this action is unavailable.";
    if (!canAccess(resource, action)) return `Your role does not include ${resource} ${action} permission.`;
    return null;
  };

  /** Props for a control that performs `action`: disabled with its reason when not allowed. */
  const gate = (action: A) => {
    const why = reason(action);
    return why ? { disabled: true, title: why } : {};
  };

  const latest = useRef(allowed);
  latest.current = allowed;
  const stillAllowed = (action: A) => latest.current(action);

  return { allowed, reason, gate, stillAllowed };
}

export function useInfrastructureActions() {
  return usePermissionGate<InfrastructureAction>("infrastructure");
}
