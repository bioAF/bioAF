"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useInfrastructureActions } from "@/hooks/useInfrastructureActions";
import { useInfrastructureOperations } from "@/hooks/useInfrastructureOperations";
import { useResource } from "@/hooks/useResource";
import { logError } from "@/lib/errorReporting";
import { isActive, type OperationView } from "@/lib/infrastructureOperations";
import { infrastructure, type DestructiveResourceInfo } from "@/lib/infrastructure";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";

interface Props {
  // Called once the confirmed update is accepted so the parent can refresh status.
  onApplyStarted?: () => void;
}

function newKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function SkippedList({ resources }: { resources: DestructiveResourceInfo[] }) {
  return (
    <div className="mt-2 rounded border border-red-200 bg-red-50 p-3">
      <p className="text-sm font-medium text-red-800">
        Will NOT be applied: would destroy or replace existing resources ({resources.length})
      </p>
      <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-red-900">
        {resources.map((r) => (
          <li key={r.address}>
            <span className="font-mono uppercase">{r.action}</span> {r.description || r.address}
            {r.stateful ? <span className="ml-1 font-semibold">(stored data)</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * "Check for Infrastructure Updates" (mockup M1). The popup confirmation is the only approval:
 * once accepted, one operation plans every deployed module and applies the additive changes
 * automatically. Changes that would delete or replace an existing resource are listed and never
 * applied. The card shows the outcome of the operation it started, and nothing older.
 */
export function InfraUpdatesCard({ onApplyStarted }: Props) {
  const actions = useInfrastructureActions();
  const deployReason = actions.reason("deploy");
  const operations = useInfrastructureOperations();
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const [operationId, setOperationId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const outcome = useResource<OperationView>(
    operationId === null ? null : `infrastructure:update-outcome:${operationId}`,
    useCallback(
      (signal: AbortSignal) => api.get<OperationView>(`/api/v1/infrastructure/operations/${operationId}`, { signal }),
      [operationId],
    ),
    {
      poll: ({ data }) => (data && !isActive(data) ? false : 5_000),
      onError: (e) => logError("reading the infrastructure update outcome", e),
    },
  );
  const op = outcome.data;
  const settled = op && !isActive(op) ? op : null;
  const skipped = ((settled?.partial_effects as { destructive?: DestructiveResourceInfo[] } | null)?.destructive ??
    []) as DestructiveResourceInfo[];

  async function confirm() {
    setConfirming(false);
    // Permission and availability are checked again: either may have changed while the popup was open.
    if (!actions.stillAllowed("deploy")) return;
    setStarting(true);
    setError(null);
    try {
      const accepted = await infrastructure.checkUpdates(newKey());
      setOperationId(accepted.operation_id);
      operations?.refresh();
      operations?.open(accepted.operation_id);
      onApplyStarted?.();
    } catch (e) {
      if (operations?.showConflict(e, "check for infrastructure updates")) return;
      setError(e instanceof Error ? e.message : "Could not check for updates.");
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="mb-6 rounded-lg border border-gray-200 bg-white p-4 shadow">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Infrastructure updates</h3>
          <p className="mt-1 max-w-2xl text-xs text-gray-600">
            Compare deployed infrastructure with this version of bioAF and add any new resources it needs. Changes
            that would delete or replace existing resources are listed, never applied here.
          </p>
        </div>
        <Button
          size="sm"
          className="ml-4 shrink-0"
          onClick={() => setConfirming(true)}
          disabled={starting || deployReason !== null}
          title={deployReason ?? undefined}
        >
          {starting ? "Starting..." : "Check for Infrastructure Updates"}
        </Button>
      </div>

      {error && <div className="mt-3 text-sm text-red-700">{error}</div>}

      {settled?.message && (
        <div className={`mt-3 text-sm ${settled.status === "succeeded" ? "text-green-700" : "text-red-700"}`}>
          {settled.message}
        </div>
      )}
      {settled && skipped.length > 0 && <SkippedList resources={skipped} />}

      <Modal
        open={confirming}
        title="Check for infrastructure updates"
        onClose={() => setConfirming(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button onClick={confirm} disabled={deployReason !== null} title={deployReason ?? undefined}>
              Check and apply updates
            </Button>
          </>
        }
      >
        <div className="space-y-2 text-sm text-gray-700">
          <p>
            bioAF will compare your deployed infrastructure with this version of bioAF and add anything new it needs,
            such as a new storage bucket.
          </p>
          <p>
            Existing resources are never deleted or replaced by this check. If an update would delete or replace
            something, bioAF lists it for you and leaves it unchanged.
          </p>
          <p>
            While this runs, deploy, teardown, storage, and image build actions are unavailable to every administrator.
          </p>
        </div>
      </Modal>
    </div>
  );
}
