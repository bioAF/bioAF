"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { usePermissions } from "@/hooks/usePermissions";
import { logError } from "@/lib/errorReporting";
import { conflictCopy, type ActiveOperation, type OperationView } from "@/lib/infrastructureOperations";
import {
  InfrastructureOperationsContext as Context,
  type InfrastructureOperationsState,
} from "@/hooks/useInfrastructureOperations";

export { useInfrastructureOperations } from "@/hooks/useInfrastructureOperations";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";
import { OperationStatusModal } from "./OperationStatusModal";

const ACTIVE_PATH = "/api/v1/infrastructure/operations/active";
const POLL_MS = 5_000;

function busyOperation(error: unknown): OperationView | null {
  if (!(error instanceof ApiError) || error.status !== 409 || error.code !== "infrastructure_busy") return null;
  const operation = (error.details as { operation?: OperationView } | undefined)?.operation;
  return operation ?? null;
}

/**
 * One source of infrastructure availability for the banner, every infrastructure control and the
 * status popup. Reads are polled; a pending or failed read leaves actions unavailable. The server
 * is still the guard: a stale page gets 409 and the M6 explanation.
 */
export function InfrastructureOperationsProvider({ children }: { children: ReactNode }) {
  const { canAccess, loading, failed: permissionsFailed } = usePermissions();
  const isAdmin =
    !loading && !permissionsFailed && (canAccess("infrastructure", "deploy") || canAccess("infrastructure", "configure"));

  const read = useResource<ActiveOperation>(
    "infrastructure:operations:active",
    useCallback((signal: AbortSignal) => api.get<ActiveOperation>(ACTIVE_PATH, { signal }), []),
    { poll: POLL_MS, onError: (e) => logError("reading infrastructure operation status", e) },
  );
  const failed = read.error != null;
  const active = !failed && read.data ? read.data : null;

  const [opened, setOpened] = useState<{ id: number; fromBanner: boolean } | null>(null);
  const [conflict, setConflict] = useState<{ op: OperationView; requested: string } | null>(null);

  const refresh = useCallback(() => void read.refresh(), [read]);
  const open = useCallback(
    (operationId: number, options?: { fromBanner?: boolean }) =>
      setOpened({ id: operationId, fromBanner: Boolean(options?.fromBanner) }),
    [],
  );
  const showConflict = useCallback(
    (error: unknown, requested: string) => {
      const op = busyOperation(error);
      if (!op) return false;
      setConflict({ op, requested });
      void read.refresh();
      return true;
    },
    [read],
  );

  const value = useMemo<InfrastructureOperationsState>(
    () => ({ active, failed, isAdmin, open, showConflict, refresh }),
    [active, failed, isAdmin, open, showConflict, refresh],
  );

  const copy = conflict ? conflictCopy(conflict.op, conflict.requested) : null;

  return (
    <Context.Provider value={value}>
      {children}
      {opened && isAdmin && (
        <OperationStatusModal
          operationId={opened.id}
          openedFromBanner={opened.fromBanner}
          onClose={() => setOpened(null)}
          onSettled={refresh}
        />
      )}
      {conflict && copy && (
        <Modal
          open
          title={copy.title}
          onClose={() => setConflict(null)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setConflict(null)}>
                Close
              </Button>
              <Button
                onClick={() => {
                  setConflict(null);
                  open(conflict.op.id, { fromBanner: true });
                }}
              >
                {copy.view}
              </Button>
            </>
          }
        >
          <p className="text-sm text-gray-700">{copy.body}</p>
          <p className="mt-2 text-sm text-gray-500">{copy.secondary}</p>
        </Modal>
      )}
    </Context.Provider>
  );
}
