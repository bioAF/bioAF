"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";

/**
 * Shown when a study is held in `samples_mismatch`: a sample the scientist picked was not fetched
 * (embargoed / withdrawn / failed download), so the reproduction is parked BEFORE any compute is
 * spent. The approver decides: run with the samples that were fetched (the design was already
 * rewritten to them), or stop. Both actions return the updated study for the page to re-render.
 */
export function SamplesMismatchNotice({
  studyId,
  failureReason,
  onChanged,
}: {
  studyId: number;
  failureReason?: string | null;
  onChanged: (updated: unknown) => void;
}) {
  const { canAccess } = usePermissions();
  const [busy, setBusy] = useState<null | "run" | "stop">(null);
  const [error, setError] = useState<string | null>(null);

  const base = `/api/validation-studies/${studyId}`;
  const btn = "rounded px-4 py-2 text-sm font-medium disabled:opacity-50";

  async function run(which: "run" | "stop", action: () => Promise<unknown>) {
    setBusy(which);
    setError(null);
    try {
      onChanged(await action());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div role="alert" className="rounded border border-amber-300 bg-amber-50 p-4">
      <h3 className="text-sm font-semibold text-amber-800">Some samples could not be fetched</h3>
      <p className="mt-1 text-sm text-gray-700">
        {failureReason || "A sample you selected was not fetched."}
      </p>
      <p className="mt-1 text-xs text-gray-600">
        This study is held before any compute is spent. Run with the samples that were fetched, or stop
        the reproduction.
      </p>
      {canAccess("lit_validation", "approve") && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            className={`${btn} bg-bioaf-600 text-white hover:bg-bioaf-700`}
            disabled={busy !== null}
            onClick={() => run("run", () => api.post(`${base}/override-samples`, {}))}
          >
            {busy === "run" ? "Starting..." : "Run anyway"}
          </button>
          <button
            className={`${btn} border border-gray-300 text-gray-700 hover:bg-gray-100`}
            disabled={busy !== null}
            onClick={() =>
              run("stop", () =>
                api.post(`${base}/decline`, {
                  reason: "Stopped: required samples were not fetched.",
                }),
              )
            }
          >
            {busy === "stop" ? "Stopping..." : "Stop"}
          </button>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}
