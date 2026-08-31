"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { Button } from "@/components/ui/Button";

/**
 * Shown when a study is in `error`: the infrastructure failed, not the paper.
 *
 * The distinction is the whole point of the state and it is easy to misread, because a study that
 * stops looks like a study that did not reproduce. A wrong launch parameter, an unreachable
 * reference or a dead node says nothing about the science, so this panel says so in the first line
 * and offers the way back.
 *
 * Retry resumes wherever the study's surviving work allows: with the data already fetched it goes
 * straight back to the analysis, and with nothing fetched it returns to the approval gate so the
 * re-fetch is a decision rather than a side effect. The server decides which, never this component.
 */
export function RetryNotice({
  studyId,
  failureReason,
  reapAfter,
  dataDeleted,
  onChanged,
}: {
  studyId: number;
  failureReason?: string | null;
  // When the study's downloaded data stops being kept for a retry (ISO 8601, set by the server so
  // the retention window is not restated here).
  reapAfter?: string | null;
  // The window has already closed and the data is gone, so a retry re-downloads rather than resumes.
  dataDeleted?: boolean;
  onChanged: (updated: unknown) => void;
}) {
  const { canAccess } = usePermissions();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function retry() {
    setBusy(true);
    setError(null);
    try {
      onChanged(await api.post(`/api/validation-studies/${studyId}/retry`, {}));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Retry failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="alert" className="rounded border border-amber-300 bg-amber-50 p-4">
      <h3 className="text-sm font-semibold text-amber-800">This study stopped on a technical failure</h3>
      <p className="mt-1 text-sm text-gray-700">{failureReason || "The reproduction could not be completed."}</p>
      <p className="mt-1 text-xs text-gray-600">
        This is not a result about the paper and not a verdict on whether its finding reproduces.{" "}
        {dataDeleted
          ? "Retrying runs this study again from the start."
          : "Retrying picks up from the work already done: data that was already downloaded is reused, and only the steps that failed run again."}
      </p>
      {dataDeleted ? (
        <p className="mt-2 text-xs text-gray-600">
          The data downloaded for this study has been deleted to free storage. You can still retry it, and
          it will download the data again.
        </p>
      ) : reapAfter ? (
        <p className="mt-2 text-xs text-gray-600">
          The data downloaded for this study is kept until {new Date(reapAfter).toLocaleDateString()}. Retry
          before then and it is reused; after that it is deleted and a retry downloads it again.
        </p>
      ) : null}
      {canAccess("lit_validation", "approve") && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button busy={busy} busyLabel="Retrying..." onClick={retry}>
            Retry
          </Button>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}
