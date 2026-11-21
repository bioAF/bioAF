"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { usePermissions } from "@/hooks/usePermissions";
import { logError } from "@/lib/errorReporting";
import { Alert } from "@/components/shared/Alert";
import { Button } from "@/components/ui/Button";

interface PendingUpload {
  upload_id: string;
  filename: string;
  reference: string;
  state: "retrying" | "needs_attention";
  experiment_name?: string | null;
  uploaded_by?: string | null;
}

/**
 * Uploads whose bytes are stored but not filed into their experiment (mockup M12). Shown to roles
 * that can edit files (admins and comp_bio, decision M12-a); each can be retried here.
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
      <p>Received but not yet filed into its experiment.</p>
      <ul className="mt-2 space-y-1">
        {uploads.map((u) => (
          <li key={u.upload_id} className="flex items-center justify-between gap-3">
            <span>
              <span className="font-mono text-xs">{u.filename}</span>
              <span className="block text-xs">
                {[u.uploaded_by, u.experiment_name, u.reference].filter(Boolean).join(" · ")}
              </span>
            </span>
            <Button size="sm" variant="secondary" onClick={() => void retry(u)} disabled={retrying === u.upload_id}>
              Retry
            </Button>
          </li>
        ))}
      </ul>
      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
    </Alert>
  );
}
