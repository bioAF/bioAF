"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { usePermissions } from "@/hooks/usePermissions";
import { logError } from "@/lib/errorReporting";
import { Alert } from "@/components/shared/Alert";
import { Button } from "@/components/ui/Button";
import { ReleaseUploadDialog } from "@/components/files/ReleaseUploadDialog";

interface PendingUpload {
  upload_id: string;
  filename: string;
  reference: string;
  // "stopping": cancelled, storage has not confirmed it stopped; "unconfirmed": storage could not
  // confirm it stopped, so an administrator may release it (11G).
  state: "retrying" | "needs_attention" | "stopping" | "unconfirmed";
  experiment_name?: string | null;
  uploaded_by?: string | null;
}

const STATE_LINE: Partial<Record<PendingUpload["state"], string>> = {
  stopping: "Cancelled. Waiting for storage to confirm the upload stopped.",
  unconfirmed: "Storage could not confirm this upload stopped.",
};

/**
 * Uploads that need following (mockup M12, storage lifecycle 11G): bytes stored but not filed into
 * their experiment (each can be retried here), cancelled uploads storage has not confirmed stopped,
 * and uploads storage could not confirm, which infrastructure administrators can release. Shown to
 * roles that can edit files (admins and comp_bio, decision M12-a).
 */
export function UploadsAttentionPanel() {
  const { canAccess, loading, failed } = usePermissions();
  const allowed = !loading && !failed && canAccess("files", "edit");
  const read = useResource<{ uploads: PendingUpload[] }>(
    allowed ? "files:uploads:pending" : null,
    useCallback(
      (signal: AbortSignal) => api.get<{ uploads: PendingUpload[] }>("/api/files/uploads/pending", { signal }),
      [],
    ),
    { poll: 60_000, onError: (e) => logError("reading uploads that need attention", e) },
  );
  const [retrying, setRetrying] = useState<string | null>(null);
  const [releasing, setReleasing] = useState<PendingUpload | null>(null);
  const canRelease = canAccess("infrastructure", "configure");
  const [error, setError] = useState<string | null>(null);
  const uploads = read.data?.uploads ?? [];
  if (!allowed || uploads.length === 0) return null;

  const retry = async (upload: PendingUpload) => {
    setRetrying(upload.upload_id);
    setError(null);
    try {
      await api.post(`/api/files/uploads/${upload.upload_id}/retry`, {});
    } catch (e) {
      logError("retrying an upload", e);
      setError(e instanceof Error ? e.message : "The upload could not be filed. Try again.");
    } finally {
      setRetrying(null);
      void read.refresh();
    }
  };

  const n = uploads.length;
  return (
    <Alert tone="warning" title={`${n} upload${n === 1 ? " needs" : "s need"} attention`} className="mb-4">
      {uploads.some((u) => !STATE_LINE[u.state]) && <p>Received but not yet filed into its experiment.</p>}
      <ul className="mt-2 space-y-1">
        {uploads.map((u) => (
          <li key={u.upload_id} className="flex items-center justify-between gap-3">
            <span>
              <span className="font-mono text-xs">{u.filename}</span>
              <span className="block text-xs">
                {[u.uploaded_by, u.experiment_name, u.reference].filter(Boolean).join(" · ")}
              </span>
              {STATE_LINE[u.state] && <span className="block text-xs">{STATE_LINE[u.state]}</span>}
            </span>
            {!STATE_LINE[u.state] && (
              <Button size="sm" variant="secondary" onClick={() => void retry(u)} disabled={retrying === u.upload_id}>
                Retry
              </Button>
            )}
            {u.state === "unconfirmed" && canRelease && (
              <Button size="sm" variant="secondary" onClick={() => setReleasing(u)}>
                Release
              </Button>
            )}
          </li>
        ))}
      </ul>
      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
      {releasing && (
        <ReleaseUploadDialog
          uploadId={releasing.upload_id}
          filename={releasing.filename}
          onClose={() => setReleasing(null)}
          onReleased={() => {
            setReleasing(null);
            void read.refresh();
          }}
        />
      )}
    </Alert>
  );
}
