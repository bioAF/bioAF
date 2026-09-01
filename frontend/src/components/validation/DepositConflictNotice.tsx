"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { Button } from "@/components/ui/Button";
import { logError } from "@/lib/errorReporting";

export interface DepositConflict {
  message: string;
  // The pipeline that reads what the public record says this data is, when the record names one.
  // Null for a data type that is declared but too broad to point at a single pipeline, and then the
  // only way on is the override.
  suggested_pipeline_key?: string | null;
  library_strategy?: string | null;
  // Set once someone has answered the conflict by overruling the record. The conflict is still
  // reported, because the run really does carry it, but it stops asking.
  override?: { user_id?: number | null; at?: string | null; reason?: string | null } | null;
}

/**
 * The C1 gate's fatal blocker, and the two ways past it.
 *
 * bioAF picked a tool from the paper's own methods, then read the public record for the dataset the
 * study is pinned to. The record says the data is one type; the tool reads another. Running it would
 * spend the compute and answer confidently about the wrong thing, so approval is refused.
 *
 * It used to be refused and nothing more. The blocker rendered as one bullet among advisory ones,
 * Approve stayed enabled, clicking it returned a 400, and the only remaining control was Decline,
 * which is terminal. Both ways out live here now, in the order they should be taken: correct the
 * plan, or say the record itself is wrong and put that on the record.
 *
 * "Might", not "would": a depositor can label a series wrong, which is exactly why the override
 * exists.
 */
export function DepositConflictNotice({
  studyId,
  conflict,
  onChanged,
}: {
  studyId: number;
  conflict: DepositConflict;
  onChanged: (updated: unknown) => void;
}) {
  const { canAccess } = usePermissions();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overriding, setOverriding] = useState(false);
  const [reason, setReason] = useState("");

  const canApprove = canAccess("lit_validation", "approve");
  const suggested = conflict.suggested_pipeline_key;
  const answered = conflict.override ?? null;

  async function run(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await api.post(`/api/validation-studies/${studyId}/${path}`, body as undefined));
    } catch (e) {
      logError(`resolving the deposit conflict on study ${studyId}`, e);
      setError(e instanceof Error ? e.message : "That could not be applied.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="alert" className="rounded border border-amber-300 bg-amber-50 p-4">
      <h3 className="text-sm font-semibold text-amber-800">This plan might run the wrong tool on this data</h3>
      <p className="mt-1 text-sm text-gray-700">{conflict.message}</p>
      {answered && (
        <p className="mt-2 text-sm text-gray-700">
          Running anyway{answered.at ? ` on ${new Date(answered.at).toLocaleDateString()}` : ""}:{" "}
          {answered.reason}
        </p>
      )}
      {canApprove && !answered && (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {suggested && (
              <Button busy={busy} busyLabel="Updating..." onClick={() => run("use-deposit-pipeline")}>
                Use {suggested} instead
              </Button>
            )}
            {!overriding && (
              <button
                type="button"
                className="rounded border border-amber-400 px-4 py-2 text-sm font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
                disabled={busy}
                onClick={() => setOverriding(true)}
              >
                Run it anyway
              </button>
            )}
          </div>
          {overriding && (
            <div className="mt-3 space-y-2">
              <label className="block text-xs font-medium text-gray-700" htmlFor={`override-reason-${studyId}`}>
                Why should this run anyway?
              </label>
              <input
                id={`override-reason-${studyId}`}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="For example: the depositor labelled this series wrong."
                className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
              />
              <p className="text-xs text-gray-600">
                This is kept with the study, so a result that disagrees with the paper can be read against it.
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  busy={busy}
                  busyLabel="Recording..."
                  disabled={reason.trim().length < 3}
                  onClick={() => run("override-deposit", { reason: reason.trim() })}
                >
                  Record and run anyway
                </Button>
                <button
                  type="button"
                  className="px-3 py-2 text-sm text-gray-600 hover:text-gray-900"
                  onClick={() => {
                    setOverriding(false);
                    setReason("");
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}
