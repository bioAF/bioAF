"use client";

import { createContext, useContext } from "react";
import { blockedReason, type ActiveOperation } from "@/lib/infrastructureOperations";

export interface InfrastructureOperationsState {
  /** The confirmed answer, or null while it is unknown (not yet read, or the last read failed). */
  active: ActiveOperation | null;
  /** The last read failed, so availability cannot be confirmed. */
  failed: boolean;
  /** Infrastructure deploy or configure: may see the operation and open its status. */
  isAdmin: boolean;
  /** Open the status popup for an operation. */
  open: (operationId: number, options?: { fromBanner?: boolean }) => void;
  /** Show the M6 refusal when ``error`` is a 409 for a busy installation; returns whether it was. */
  showConflict: (error: unknown, requested: string) => boolean;
  refresh: () => void;
}

export const InfrastructureOperationsContext = createContext<InfrastructureOperationsState | null>(null);

/** The shared operation state, or null outside the app shell (tests of a single control). */
export function useInfrastructureOperations(): InfrastructureOperationsState | null {
  return useContext(InfrastructureOperationsContext);
}

/** Whether a conflicting infrastructure action is available, and if not, why. */
export function useInfrastructureAvailability(): { reason: string | null } {
  const ctx = useInfrastructureOperations();
  // Outside the shell there is no shared source to consult; the server still refuses conflicts.
  if (ctx === null) return { reason: null };
  return { reason: blockedReason(ctx.active) };
}
