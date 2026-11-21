"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { usePermissionGate } from "@/hooks/useInfrastructureActions";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import {
  footerHint,
  isActive,
  operationTitle,
  shortTime,
  stepStateText,
  type OperationStep,
  type OperationView,
} from "@/lib/infrastructureOperations";
import { Modal } from "@/components/shared/Modal";
import { Alert } from "@/components/shared/Alert";
import { Button } from "@/components/ui/Button";

const POLL_MS = 3_000;

const STEP_TONES: Record<string, string> = {
  done: "text-green-700",
  failed: "text-red-700",
  interrupted: "text-amber-700",
  stopped: "text-amber-700",
  running: "text-bioaf-700",
};

function StepRow({ step, resources }: { step: OperationStep; resources: OperationView["resources"] }) {
  const planned = resources?.planned ?? [];
  const completed = resources?.completed ?? [];
  const showResources = step.state === "running" && planned.length > 0;
  return (
    <li className="py-1.5">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="text-gray-800">{step.label}</span>
        <span className={`text-xs font-medium ${STEP_TONES[step.state] ?? "text-gray-500"}`}>{stepStateText(step)}</span>
      </div>
      {step.detail && <p className="mt-0.5 text-xs text-gray-600">{step.detail}</p>}
      {showResources && (
        <ul className="mt-1 max-h-40 overflow-y-auto pl-3 text-xs text-gray-500">
          {planned.map((address) => (
            <li key={address} className="flex justify-between gap-2 font-mono">
              <span>{address}</span>
              <span>{completed.includes(address) ? "Done" : "Queued"}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function RecoveryForm({ op, onRecovered }: { op: OperationView; onRecovered: () => void }) {
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/v1/infrastructure/operations/${op.id}/recover`, { reason: reason.trim(), confirm: true });
      onRecovered();
    } catch (e) {
      logError("recording infrastructure recovery", e);
      setError(e instanceof ApiError ? e.message : "The recovery could not be recorded. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-4 space-y-3 border-t border-gray-200 pt-4">
      <p className="text-sm text-gray-700">
        bioAF could not confirm that Terraform stopped. Starting another action while it might still run could damage
        your infrastructure records.
      </p>
      <p className="text-sm text-gray-700">
        On the bioAF server, check that no Terraform process is running, for example with{" "}
        <code className="rounded bg-gray-100 px-1">ps -ef | grep &apos;[t]erraform&apos;</code>. Then record the recovery
        below.
      </p>
      <label className="block text-sm font-medium text-gray-700" htmlFor={`recover-reason-${op.id}`}>
        Reason (saved in the audit log)
      </label>
      <textarea
        id={`recover-reason-${op.id}`}
        rows={2}
        className="w-full rounded border border-gray-300 p-2 text-sm"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <label className="flex items-start gap-2 text-sm text-gray-700">
        <input type="checkbox" className="mt-1" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        <span>I confirmed that this Terraform process is no longer running.</span>
      </label>
      {error && <Alert tone="error">{error}</Alert>}
      <div className="flex justify-end">
        <Button variant="danger" onClick={submit} disabled={busy || !confirmed || !reason.trim()}>
          Mark as recovered
        </Button>
      </div>
    </div>
  );
}

/**
 * The status popup for one infrastructure operation (mockups M2, M5, M7, M8, M9, M10). Every
 * administrator who opens an operation sees the same steps and outcome. Closing it changes
 * nothing about the operation.
 */
export function OperationStatusModal({
  operationId,
  openedFromBanner,
  onClose,
  onSettled,
}: {
  operationId: number;
  openedFromBanner: boolean;
  onClose: () => void;
  onSettled: () => void;
}) {
  const configure = usePermissionGate<"configure">("infrastructure");
  const read = useResource<OperationView>(
    `infrastructure:operation:${operationId}`,
    useCallback(
      (signal: AbortSignal) => api.get<OperationView>(`/api/v1/infrastructure/operations/${operationId}`, { signal }),
      [operationId],
    ),
    {
      poll: ({ data }) => (data && !isActive(data) ? false : POLL_MS),
      onError: (e) => logError("reading infrastructure operation", e),
    },
  );
  const op = read.data;

  // Opening an interrupted outcome is what ends its one-time banner (M9).
  const marked = useRef(false);
  useEffect(() => {
    if (!op || marked.current || op.status !== "interrupted" || op.outcome_seen_at) return;
    marked.current = true;
    api
      .post(`/api/v1/infrastructure/operations/${op.id}/seen`, {})
      .then(onSettled)
      .catch((e) => logError("recording that an operation outcome was seen", e));
  }, [op, onSettled]);

  // Refresh shared availability once this operation settles.
  const settled = useRef(false);
  useEffect(() => {
    if (op && !isActive(op) && !settled.current) {
      settled.current = true;
      onSettled();
    }
  }, [op, onSettled]);

  const title = op ? operationTitle(op) : "Infrastructure operation";
  const hint = op ? footerHint(op, { openedFromBanner }) : null;

  return (
    <Modal
      open
      title={title}
      onClose={onClose}
      size="lg"
      footer={
        <div className="flex w-full items-center justify-between gap-4">
          <span className="text-xs text-gray-500">{hint}</span>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      }
    >
      {!op ? (
        read.error ? (
          <Alert tone="error">{loadFailureMessage("The operation status")}</Alert>
        ) : (
          <p className="text-sm text-gray-500">Loading…</p>
        )
      ) : (
        <div>
          <div className="mb-3 flex items-baseline justify-between gap-3">
            <span className="text-xs font-medium text-gray-500">Operation #{op.id}</span>
            {op.started_by && (
              <span className="text-xs text-gray-500">
                Started by {op.started_by} at {shortTime(op.accepted_at)}
              </span>
            )}
          </div>
          <ul className="divide-y divide-gray-100">
            {(op.steps ?? []).map((step) => (
              <StepRow key={step.key} step={step} resources={op.resources ?? null} />
            ))}
          </ul>
          {op.message && op.status !== "running" && (
            <Alert tone={op.status === "succeeded" ? "success" : op.status === "failed" ? "error" : "warning"} className="mt-4">
              {op.message}
            </Alert>
          )}
          {op.status === "recovery_required" && configure.allowed("configure") && (
            <RecoveryForm op={op} onRecovered={() => void read.refresh().then(onSettled)} />
          )}
        </div>
      )}
    </Modal>
  );
}
