"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import { Modal } from "@/components/shared/Modal";
import { Alert } from "@/components/shared/Alert";
import { Button } from "@/components/ui/Button";

interface Props {
  uploadId: string;
  filename: string;
  onClose: () => void;
  onReleased: () => void;
  /** Called with the reason when the stop was refused because the upload's status changed. */
  onRefused?: (message: string) => void;
}

/**
 * Stop waiting for an upload bioAF cannot confirm has stopped, so it no longer blocks Destroy
 * Storage (storage lifecycle 11G, W3). Needs a reason and an explicit acknowledgement; recorded in
 * the audit log. A refusal says what changed (W4) and refreshes the caller's list.
 */
export function ReleaseUploadDialog({ uploadId, filename, onClose, onReleased, onRefused }: Props) {
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/v1/infrastructure/storage/writes/${encodeURIComponent(uploadId)}/release`, {
        reason: reason.trim(),
        acknowledge_transfer_may_exist: true,
      });
      onReleased();
    } catch (e) {
      logError("stopping the wait for an upload", e);
      if (e instanceof ApiError && e.status === 409 && onRefused) {
        onRefused(e.message);
        return;
      }
      setError(e instanceof ApiError ? e.message : "This could not be recorded. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      title="Stop waiting for this upload?"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Keep waiting
          </Button>
          <Button variant="danger" onClick={submit} disabled={busy || !acknowledged || !reason.trim()}>
            Stop waiting
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm text-gray-700">
        <p>
          bioAF cannot tell whether {filename} is still uploading. If you stop waiting, this upload will no longer block
          Destroy Storage.
        </p>
        <p>
          Destroying storage could delete the file before its upload finishes. This step does not delete anything. Your
          decision and reason will be recorded.
        </p>
        <label className="block font-medium" htmlFor={`release-reason-${uploadId}`}>
          Reason
        </label>
        <textarea
          id={`release-reason-${uploadId}`}
          rows={2}
          className="w-full rounded border border-gray-300 p-2"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            className="mt-1"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
          />
          <span>I understand that Destroy Storage could delete this file while it is still uploading.</span>
        </label>
        {error && <Alert tone="error">{error}</Alert>}
      </div>
    </Modal>
  );
}
