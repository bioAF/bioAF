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
}

/**
 * Release an upload storage could not confirm stopped, so it no longer blocks Destroy Storage
 * (storage lifecycle 11G). Needs a reason and an explicit acknowledgement; recorded in the audit log.
 */
export function ReleaseUploadDialog({ uploadId, filename, onClose, onReleased }: Props) {
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
      logError("releasing an upload storage could not confirm", e);
      setError(e instanceof ApiError ? e.message : "The upload could not be released. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      title="Release this upload?"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Keep waiting
          </Button>
          <Button variant="danger" onClick={submit} disabled={busy || !acknowledged || !reason.trim()}>
            Release upload
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm text-gray-700">
        <p>
          bioAF could not confirm that {filename} stopped uploading. Releasing it lets Destroy Storage go ahead even though
          the transfer might still exist. This is recorded in the audit log.
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
          <span>I understand the transfer might still exist.</span>
        </label>
        {error && <Alert tone="error">{error}</Alert>}
      </div>
    </Modal>
  );
}
