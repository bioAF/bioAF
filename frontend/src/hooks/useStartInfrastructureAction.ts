"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { useInfrastructureOperations } from "@/hooks/useInfrastructureOperations";

function newKey(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Start a confirmed infrastructure action (mockups M2, M6).
 *
 * One confirmation sends one Idempotency-Key, so a retry of the same request replays the same
 * operation. On acceptance the status popup opens on that operation and every page's availability
 * refreshes; when another action started first, the M6 explanation replaces the request. Returns
 * the accepted operation ID, or null when it was refused as a conflict. Other failures are thrown.
 */
export function useStartInfrastructureAction() {
  const ops = useInfrastructureOperations();
  return useCallback(
    async (path: string, body: unknown, requested: string): Promise<number | null> => {
      try {
        const response = await api.post<{ operation_id?: number }>(path, body, { idempotencyKey: newKey() });
        ops?.refresh();
        if (response?.operation_id != null) ops?.open(response.operation_id);
        return response?.operation_id ?? null;
      } catch (error) {
        if (ops?.showConflict(error, requested)) return null;
        throw error;
      }
    },
    [ops],
  );
}
