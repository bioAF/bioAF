"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { VALIDATION_CLASSIFICATIONS } from "@/lib/validationClassification";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";

// The human gates on a validation study, rendered per state. `requested` needs a Read (B1 fetches the
// full text by DOI, or paste a body); `plan_ready` is the C1 approve/decline gate; `comparing` is the
// manual classification gate (Phase 1 keeps comparison manual). The automated stages in between are
// advanced by the background driver, so they surface no action here. Each action returns the updated
// study, handed back via onChanged so the page can re-render without a full refetch.
export function ValidationStudyActions({
  study,
  onChanged,
  suggestedClassification,
}: {
  study: { id: number; state: string };
  onChanged: (updated: unknown) => void;
  // The classifier's (E2/E3/E4) suggested verdict at comparing; pre-selects the Classify control so the
  // human ratifies with one click (or overrides).
  suggestedClassification?: string | null;
}) {
  const { canAccess } = usePermissions();
  const [showApprove, setShowApprove] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fullText, setFullText] = useState("");
  const [declineReason, setDeclineReason] = useState("");
  const [classification, setClassification] = useState(() =>
    suggestedClassification && VALIDATION_CLASSIFICATIONS.some((c) => c.value === suggestedClassification)
      ? suggestedClassification
      : VALIDATION_CLASSIFICATIONS[0].value,
  );

  const canRequest = canAccess("lit_validation", "request");
  const canApprove = canAccess("lit_validation", "approve");

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      const updated = await action();
      onChanged(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  const base = `/api/validation-studies/${study.id}`;
  const btn = "rounded px-4 py-2 text-sm font-medium disabled:opacity-50";

  let controls: React.ReactNode = null;

  if (study.state === "requested" && canRequest) {
    controls = (
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <button
            className={`${btn} bg-bioaf-600 text-white hover:bg-bioaf-700`}
            disabled={busy}
            onClick={() =>
              run(() => api.post(`${base}/read`, fullText.trim() ? { full_text: fullText } : {}))
            }
          >
            {busy ? "Reading paper..." : "Read paper"}
          </button>
          <span className="text-xs text-gray-500">
            Fetches the full text by DOI and extracts the reproduction plan (may take a moment).
          </span>
        </div>
        <textarea
          value={fullText}
          onChange={(e) => setFullText(e.target.value)}
          placeholder="Optional: paste the full text if the paper is not open access."
          className="w-full rounded border border-gray-300 p-2 text-sm"
          rows={3}
        />
      </div>
    );
  } else if (study.state === "plan_ready" && canApprove) {
    controls = (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <button
            className={`${btn} bg-green-600 text-white hover:bg-green-700`}
            disabled={busy}
            onClick={() => setShowApprove(true)}
          >
            {busy ? "Working..." : "Approve plan"}
          </button>
          <input
            value={declineReason}
            onChange={(e) => setDeclineReason(e.target.value)}
            placeholder="Reason (optional)"
            className="rounded border border-gray-300 px-3 py-2 text-sm"
          />
          <button
            className={`${btn} border border-red-300 text-red-700 hover:bg-red-50`}
            disabled={busy}
            onClick={() =>
              run(() => api.post(`${base}/decline`, declineReason.trim() ? { reason: declineReason } : {}))
            }
          >
            Decline
          </button>
        </div>
        <p className="text-xs text-gray-500">
          Approving spends compute: it fetches the data and runs the reproduction pipeline.
        </p>
        <ConfirmDialog
          open={showApprove}
          title="Approve this plan?"
          message={
            <>
              <p>
                This fetches the paper's data and runs the reproduction pipeline on it.
                That spends compute on your cloud account, and the spend cannot be
                recovered once the run starts.
              </p>
              <p>The study stays held until you approve, so nothing has been charged yet.</p>
            </>
          }
          confirmLabel="Approve and run"
          busy={busy}
          onConfirm={() => {
            setShowApprove(false);
            run(() => api.post(`${base}/approve`, undefined));
          }}
          onCancel={() => setShowApprove(false)}
        />
      </div>
    );
  } else if (study.state === "comparing" && canApprove) {
    controls = (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={classification}
            onChange={(e) => setClassification(e.target.value)}
            className="rounded border border-gray-300 px-3 py-2 text-sm"
            aria-label="Classification"
          >
            {VALIDATION_CLASSIFICATIONS.map((c) => (
              <option key={c.value} value={c.value} title={c.description}>
                {c.label}
              </option>
            ))}
          </select>
          <button
            className={`${btn} bg-bioaf-600 text-white hover:bg-bioaf-700`}
            disabled={busy}
            onClick={() => run(() => api.post(`${base}/classify`, { classification }))}
          >
            {busy ? "Working..." : "Record classification"}
          </button>
        </div>
        <p className="text-xs text-gray-500">
          Read the computed-vs-claimed evidence below, then record the verdict.
        </p>
      </div>
    );
  }

  if (!controls && !error) return null;

  return (
    <div>
      {controls}
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}
