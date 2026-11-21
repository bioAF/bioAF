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

/** What finished work means for each action, shown before a cancellation (M16). */
function cancelConsequence(op: OperationView): string | null {
  if (op.action === "storage_destroy") return "Those objects are permanently deleted.";
  if (op.action === "compute_deploy") {
    return "Created resources stay in place. Run Teardown to remove them, or Deploy again to finish.";
  }
  return null;
}

function CancelConfirm({
  op,
  onKeep,
  onCancelled,
}: {
  op: OperationView;
  onKeep: () => void;
  onCancelled: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const action = `Cancel ${op.label.toLowerCase()}`;
  const done = (op.steps ?? []).filter((s) => s.state === "done" && s.key !== "accepted" && s.key !== "prerequisites");
  const consequence = cancelConsequence(op);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/v1/infrastructure/operations/${op.id}/cancel`, {});
      onCancelled();
    } catch (e) {
      logError("cancelling an infrastructure operation", e);
      setError(e instanceof ApiError ? e.message : "The cancellation could not be requested. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      title={`${action}?`}
      onClose={onKeep}
      footer={
        <>
          <Button variant="secondary" onClick={onKeep} disabled={busy}>
            Keep running
          </Button>
          <Button variant="danger" onClick={submit} disabled={busy}>
            {action}
          </Button>
        </>
      }
    >
      <p className="text-xs text-gray-500">Operation #{op.id}</p>
      <p className="mt-2 text-sm text-gray-700">bioAF will stop as soon as it safely can. Work already done is not undone.</p>
      {done.length > 0 && (
        <div className="mt-3 rounded border border-gray-200 bg-gray-50 p-3 text-sm">
          <p className="font-medium text-gray-800">Already done</p>
          <ul className="mt-1 list-disc pl-5 text-gray-700">
            {done.map((s) => (
              <li key={s.key}>{s.label}</li>
            ))}
          </ul>
          {consequence && <p className="mt-2 text-gray-700">{consequence}</p>}
        </div>
      )}
      {done.length === 0 && consequence && <p className="mt-3 text-sm text-gray-700">{consequence}</p>}
      {error && (
        <Alert tone="error" className="mt-3">
          {error}
        </Alert>
      )}
    </Modal>
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

  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const title = op ? operationTitle(op) : "Infrastructure operation";
  const hint = op ? footerHint(op, { openedFromBanner }) : null;
  // Any admin with infrastructure configure may cancel any active operation (M16-a). This is the
  // operation's own control, so it stays available while the operation holds exclusion.
  const canCancel =
    op !== undefined && ["accepted", "running", "stopping"].includes(op.status) && configure.allowed("configure");
  const stopping = op?.status === "stopping" || Boolean(op?.cancel_requested_at && op && isActive(op));

  return (
    <Modal
      open
      title={title}
      onClose={onClose}
      size="lg"
      footer={
        <div className="flex w-full items-center justify-between gap-4">
          <span className="text-xs text-gray-500">{hint}</span>
          <div className="flex items-center gap-3">
            {canCancel && op && (
              <button
                type="button"
                className="text-sm font-medium text-red-600 hover:text-red-800 disabled:opacity-60"
                disabled={stopping}
                onClick={() => setConfirmingCancel(true)}
              >
                {stopping ? "Stopping..." : `Cancel ${op.label.toLowerCase()}`}
              </button>
            )}
            <Button variant="secondary" onClick={onClose}>
              Close
            </Button>
          </div>
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
          {stopping && (
            <p className="mb-2 text-sm text-amber-700">Stopping: waiting for Terraform to save its state.</p>
          )}
          <ul className="divide-y divide-gray-100">
            {(op.steps ?? []).map((step) => (
              <StepRow key={step.key} step={step} resources={op.resources ?? null} />
            ))}
          </ul>
          {op.status === "cancelled" && op.cancel_requested_by && (
            <p className="mt-3 text-sm text-gray-600">
              Cancelled by {op.cancel_requested_by} at {shortTime(op.cancel_requested_at)}.
            </p>
          )}
          {op.message && op.status !== "running" && op.status !== "stopping" && op.status !== "cancelled" && (
            <Alert tone={op.status === "succeeded" ? "success" : op.status === "failed" ? "error" : "warning"} className="mt-4">
              {op.message}
            </Alert>
          )}
          {op.status === "recovery_required" && configure.allowed("configure") && (
            <RecoveryForm op={op} onRecovered={() => void read.refresh().then(onSettled)} />
          )}
          {confirmingCancel && (
            <CancelConfirm
              op={op}
              onKeep={() => setConfirmingCancel(false)}
              onCancelled={() => {
                setConfirmingCancel(false);
                void read.refresh();
                onSettled();
              }}
            />
          )}
        </div>
      )}
    </Modal>
  );
}
