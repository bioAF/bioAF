"use client";

import { useEffect, useState } from "react";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";

/**
 * The stack's destructive confirmations. Each owns only its consent (a checkbox, a typed phrase),
 * which starts empty every time it opens; the page decides what confirming does and checks the
 * permission again when it does. Showing one never sends anything.
 */

export const DESTROY_STORAGE_PHRASE = "delete my data";

export function TeardownDialog({
  open,
  reason,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  /** Why the role may not do this, or undefined when it may. */
  reason?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    if (open) setChecked(false);
  }, [open]);
  return (
  <Modal
    open={open}
    title="Teardown Compute Stack"
    onClose={onCancel}
    size="md"
    footer={
      <>
      <button
        onClick={onCancel}
        className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
      >
        Cancel
      </button>
      <Button variant="danger"
        disabled={!checked || !!reason}
        title={reason}
        onClick={onConfirm}>
        Teardown
      </Button>
      </>
    }
  >
    <p className="text-sm text-gray-600 mb-4">
      This will destroy the Kubernetes cluster and all node pools. Active pipeline
      runs and notebook sessions will be terminated. Storage buckets and your data
      will NOT be affected.
    </p>
    <label className="flex items-start gap-2 mb-4 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => setChecked(e.target.checked)}
        className="mt-0.5"
      />
      <span className="text-sm text-gray-700">
        I understand this will terminate all running workloads
      </span>
    </label>
  </Modal>
  );
}

export function DestroyStorageDialog({
  open,
  storageLabel,
  reason,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  /** The provider's storage name (GCS, S3). */
  storageLabel: string;
  /** Why the role may not do this, or undefined when it may. */
  reason?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [checked, setChecked] = useState(false);
  const [phrase, setPhrase] = useState("");
  useEffect(() => {
    if (open) {
      setChecked(false);
      setPhrase("");
    }
  }, [open]);
  return (
  <Modal
    open={open}
    title="Destroy Storage Infrastructure"
    onClose={onCancel}
    size="md"
    footer={
      <>
      <button
        onClick={onCancel}
        className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
      >
        Cancel
      </button>
      <Button variant="danger"
        disabled={!checked || phrase !== DESTROY_STORAGE_PHRASE || !!reason}
        title={reason}
        onClick={onConfirm}>
        Destroy Storage
      </Button>
      </>
    }
  >

    <div className="bg-red-50 border border-red-200 rounded p-3 mb-4">
      <p className="text-sm font-medium text-red-800 mb-1">
        All data will be permanently deleted
      </p>
      <p className="text-xs text-red-700">
        This will permanently destroy all {storageLabel} buckets and their
        contents, including raw sample data, processed pipeline outputs, and
        results. This action cannot be undone.
      </p>
    </div>

    <label className="flex items-start gap-2 mb-4 cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => setChecked(e.target.checked)}
        className="mt-0.5"
      />
      <span className="text-sm text-gray-700">
        I understand all files stored in {storageLabel} will be permanently lost
      </span>
    </label>

    <div className="mb-4">
      <label htmlFor="type-delete-my-data-to-confirm" className="text-xs text-gray-500 block mb-1">
        Type <span className="font-mono font-medium text-gray-700">delete my data</span> to confirm
      </label>
      <input id="type-delete-my-data-to-confirm"
        type="text"
        value={phrase}
        onChange={(e) => setPhrase(e.target.value)}
        placeholder="delete my data"
        className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
      />
    </div>

  </Modal>
  );
}

export function AbortDeploymentDialog({
  open,
  busy,
  reason,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  busy: boolean;
  /** Why the role may not do this, or undefined when it may. */
  reason?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
  <Modal
    open={open}
    title="Abort Deployment"
    onClose={onCancel}
    size="md"
    footer={
      <>
      <button
        onClick={onCancel}
        className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
      >
        Continue Deploying
      </button>
      <Button variant="danger"
        onClick={onConfirm}
        disabled={busy || !!reason}
        title={reason}>
        {busy ? "Aborting..." : "Abort Deployment"}
      </Button>
      </>
    }
  >

    <div className="bg-amber-50 border border-amber-200 rounded p-3 mb-4">
      <p className="text-sm font-medium text-amber-800 mb-1">
        This may leave infrastructure in an unexpected state
      </p>
      <p className="text-xs text-amber-700">
        Aborting will cancel the current deployment. Some resources may
        have already been created and will need to be cleaned up manually
        or by re-running the deployment.
      </p>
    </div>

  </Modal>
  );
}

export function AbandonRunDialog({
  runId,
  runStatus,
  ...rest
}: {
  runId: number | null | undefined;
  runStatus: string | null | undefined;
  open: boolean;
  busy: boolean;
  reason?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { open, busy, reason, onCancel, onConfirm } = rest;
  return (
  <Modal
    open={open}
    title="Abandon Terraform Operation"
    onClose={onCancel}
    size="md"
    footer={
      <>
      <button
        onClick={onCancel}
        className="px-4 py-2 text-sm border border-gray-300 rounded hover:bg-gray-50"
      >
        Keep Running
      </button>
      <button
        onClick={onConfirm}
        disabled={busy || !!reason}
        title={reason}
        className="px-4 py-2 text-sm bg-amber-600 text-white rounded hover:bg-amber-700 disabled:opacity-50"
      >
        {busy ? "Abandoning..." : "Abandon Operation"}
      </button>
      </>
    }
  >

    <div className="bg-amber-50 border border-amber-200 rounded p-3 mb-4">
      <p className="text-sm font-medium text-amber-800 mb-1">
        This may leave infrastructure in a partial state
      </p>
      <p className="text-xs text-amber-700">
        Abandoning a running operation releases the Terraform state lock
        so you can start a new operation. If Terraform was mid-apply,
        some resources may have been created or modified. You can re-run
        the operation to reconcile.
      </p>
    </div>

    <p className="text-sm text-gray-600 mb-4">
      Run <span className="font-mono font-medium">#{runId}</span>{" "}
      ({runStatus}) will be marked as cancelled.
    </p>

  </Modal>
  );
}
