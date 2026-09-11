"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { VALIDATION_CLASSIFICATIONS } from "@/lib/validationClassification";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { RouteChooser } from "@/components/validation/RouteChooser";
import { RouteBlockedNotice } from "@/components/validation/RouteBlockedNotice";

/**
 * What step 13 established about this paper, as far as the route modal cares.
 *
 * **An UNKNOWN capability is offered, not hidden.** The three answers mean three different things
 * at the gate: YES enables a route, NO says the route cannot work and why, and UNKNOWN says "we
 * could not establish this" with the reason and STILL offers the route. Treating UNKNOWN as NO
 * would hide a workable route behind a GEO timeout; treating it as YES would promise a route that
 * may not exist.
 */
interface RouteCapability {
  value: "yes" | "no" | "unknown";
  evidence: string | null;
  failure_reason: string | null;
}

interface RouteCapabilities {
  preprocessed_data?: RouteCapability;
  raw_data?: RouteCapability;
  code_artifact?: RouteCapability;
  code_repository?: RouteCapability;
  code_sources?: { kind: string; url: string | null; identifier: string | null; [key: string]: unknown }[];
}

/**
 * What the modal may say about a capability: nothing when it is there, a plain statement when it is
 * not, and the discovery failure when we could not tell.
 */
function capabilityNote(cap: RouteCapability | undefined, absent: string): string | null {
  if (!cap || cap.value === "yes") return null;
  if (cap.value === "unknown") {
    return cap.failure_reason || "bioAF could not establish this, so the route is offered with that uncertainty.";
  }
  return absent;
}

/**
 * Which reproduction method the driver intends to try first, derived from the capabilities ALONE.
 *
 * The gate is pre-approval, so it can only state an intention. The outcome of the attempt belongs
 * to the report; a gate built to wait on an execution result would be waiting for something that
 * cannot exist yet.
 */
function intendedMethod(caps: RouteCapabilities | null | undefined): string | null {
  if (!caps) return null;
  const source = (caps.code_sources || [])[0];
  if (source) {
    return `Published code at ${source.url || source.identifier}, so the authors' own code will be attempted first.`;
  }
  if (caps.code_artifact?.value === "no" && caps.code_repository?.value === "no") {
    return "No code source was found, so an analysis will be generated from the methods the paper describes.";
  }
  return null;
}

// The human gates on a validation study, rendered per state. `requested` needs a Read (B1 fetches the
// full text by DOI, or paste a body); `plan_ready` is the C1 approve/decline gate; `comparing` is the
// manual classification gate (Phase 1 keeps comparison manual). The automated stages in between are
// advanced by the background driver, so they surface no action here. Each action returns the updated
// study, handed back via onChanged so the page can re-render without a full refetch.
// Where a run is actually in flight, and where a stopped or failed study can be picked back up.
// Both lists mirror the server's, which is what refuses the action if these ever drift.
const ACQUIRING_STATES = ["acquiring_data", "acquiring_processed", "inspecting_deposit"];

/** What bioAF is doing on a study right now: whether it moves the study on by itself, and whether a
 * worker holds it at this moment and since when. */
export interface StudyActivity {
  advancing: boolean;
  working: boolean;
  since: string | null;
}
const RESUMABLE_STATES = ["classified", "error"];

export function ValidationStudyActions({
  study,
  onChanged,
  suggestedClassification,
}: {
  study: {
    id: number;
    state: string;
    // Set when the study reached `plan_ready` from a retry with nothing left to reuse, so approving
    // pays for the download a second time.
    //
    // `capabilities` is step 13's read-time discovery. The modal used to offer all three routes
    // blind, so a person could choose the deposited-data route on a paper with no deposited matrix
    // and only find out after approving.
    evidence?: {
      awaiting_refetch_approval?: boolean | null;
      capabilities?: RouteCapabilities | null;
      // The route chosen at the button, discovered to be impossible once the paper was read. The
      // study is held at `plan_ready` and this is what says so; without it the hold is invisible.
      route_blocked?: { chosen?: string | null; reason?: string | null; action?: string | null } | null;
      // change_7.2 section 3: a hold that a person resolves. Recorded once rather than on every
      // tick, and carrying the action that ends it, because a wait with no stated way out is the
      // indefinite hold this change exists to remove.
      awaiting_choice?: { reason?: string | null; action?: string | null } | null;
      // change_7.2 section 2: an external operation an earlier worker may have started. An assisted
      // organization is asked before a second one is launched.
      awaiting_adoption?: { operation?: string | null; action?: string | null } | null;
    } | null;
    // The plan's one fatal blocker, when it has it. Approval is refused server-side while it
    // stands, so the control is not offered: DepositConflictNotice carries the two ways out.
    plan?: {
      deposit_conflict?: { message?: string; override?: unknown | null } | null;
    } | null;
    // change_7.3 section 10 item 10: what would unblock this study, from the report projection.
    resume?: { label: string; requirements: string[] } | null;
    // The route chosen at the button. A study carrying one is read by bioAF on its own, so it offers
    // no "Read paper" click: study 37's click raced the driver's read and lost.
    intended_route?: string | null;
    // What bioAF is doing on the study right now, from the server.
    activity?: StudyActivity | null;
  };
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
  // Kept apart from the decline reason: they answer different questions and end up on different
  // records, and one shared box would carry a stale sentence into the wrong one.
  const [stopReason, setStopReason] = useState("");
  // The route the approver chooses, asked in the confirm modal. `deposit` is the default on the
  // owner's instruction (2026-09-07): start from what the authors deposited, and spend on raw reads
  // only when a person asks for it.
  const [route, setRoute] = useState<"deposit" | "pipeline" | "both">("deposit");
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

  const capabilities = study.evidence?.capabilities ?? null;
  const depositNote = capabilityNote(
    capabilities?.preprocessed_data,
    "No pre-processed data was found in this study's GEO deposit, so this route has nothing to reproduce from.",
  );
  const rawNote = capabilityNote(
    capabilities?.raw_data,
    "No raw sequencing reads are published for this study, so this route has nothing to fetch.",
  );

  const base = `/api/validation-studies/${study.id}`;
  const btn = "rounded px-4 py-2 text-sm font-medium disabled:opacity-50";

  let controls: React.ReactNode = null;

  if (study.state === "requested" && study.intended_route) {
    // The driver reads this study within one tick of it being created. The page shows that work
    // instead of a click, and keeps itself current while it runs.
    const working = !!study.activity?.working;
    const since = study.activity?.since ? new Date(study.activity.since).toLocaleTimeString() : null;
    controls = (
      <div data-testid="study-activity" className="rounded border border-bioaf-200 bg-bioaf-50 p-3 text-sm text-gray-800">
        <p>
          {working
            ? `bioAF is reading the paper and extracting the reproduction plan.${since ? ` Started ${since}.` : ""}`
            : "Queued: bioAF starts reading the paper on its own within about 30 seconds."}
        </p>
        <p className="mt-1 text-xs text-gray-500">This page updates as the study moves on.</p>
      </div>
    );
  } else if (study.state === "requested" && canRequest) {
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
        <textarea aria-label="Optional: paste the full text if the paper is not open access"
          value={fullText}
          onChange={(e) => setFullText(e.target.value)}
          placeholder="Optional: paste the full text if the paper is not open access."
          className="w-full rounded border border-gray-300 p-2 text-sm"
          rows={3}
        />
      </div>
    );
  } else if (study.state === "plan_ready" && canApprove) {
    // Answered by an override is not blocked: the backend accepts the approval, so the gate
    // must offer it. Leaving it hidden made the override do nothing at all.
    const blocked = !!study.plan?.deposit_conflict && !study.plan.deposit_conflict.override;
    controls = (
      <div className="space-y-2">
        <RouteBlockedNotice blocked={study.evidence?.route_blocked} />
        <div className="flex flex-wrap items-center gap-3">
          {!blocked && (
            <button
              className={`${btn} bg-green-600 text-white hover:bg-green-700`}
              disabled={busy}
              onClick={() => setShowApprove(true)}
            >
              {busy ? "Working..." : "Approve plan"}
            </button>
          )}
          <input aria-label="Reason (optional)"
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
          Approving asks what to validate: the deposited data (minutes) or the raw reads (hours).
        </p>
        {study.evidence?.awaiting_refetch_approval && (
          <p className="text-xs text-amber-800">
            This study ran before and its downloaded data is no longer here, so approving will
            download the data again.
          </p>
        )}
        <ConfirmDialog
          open={showApprove}
          title="Approve this plan?"
          message={
            <div className="space-y-3">
              <p>Choose what to validate. The two routes answer different questions.</p>
              {intendedMethod(capabilities) && (
                <p className="text-xs text-gray-600">{intendedMethod(capabilities)}</p>
              )}

              <RouteChooser route={route} onChange={setRoute} capabilities={capabilities} idPrefix="gate" />

              {study.evidence?.awaiting_refetch_approval && route !== "deposit" && (
                <p className="text-xs text-amber-800">
                  This study has run before. The data it downloaded is no longer here, so this
                  downloads it again.
                </p>
              )}
              <p className="text-xs text-gray-500">
                The study stays held until you approve, so nothing has been charged yet.
              </p>
            </div>
          }
          confirmLabel="Approve and run"
          busy={busy}
          onConfirm={() => {
            setShowApprove(false);
            // ALWAYS send the route. The server has a default, but a caller that relies on it is
            // one default-flip away from silently spending hours of compute it did not ask for.
            run(() => api.post(`${base}/approve`, { route }));
          }}
          onCancel={() => setShowApprove(false)}
        />
      </div>
    );
  } else if (ACQUIRING_STATES.includes(study.state) && canApprove) {
    // change_7.2 section 3: no product action could stop a study in this state. `/decline` needs
    // `plan_ready` and `/classify` needs `comparing`, so studies 29 and 33 were parked in `error` by
    // a direct database write. Stopping a run must never require database access.
    controls = (
      <div className="space-y-2">
        {study.evidence?.awaiting_choice && (
          <p className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            {study.evidence.awaiting_choice.reason}. {study.evidence.awaiting_choice.action}
          </p>
        )}
        {study.evidence?.awaiting_adoption && (
          <p className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            An earlier attempt on this study may still be running. bioAF will not start a second one:{" "}
            {study.evidence.awaiting_adoption.action}.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <input
            aria-label="Why are you stopping this?"
            value={stopReason}
            onChange={(e) => setStopReason(e.target.value)}
            placeholder="Why are you stopping this? (optional)"
            className="rounded border border-gray-300 px-3 py-2 text-sm"
          />
          <button
            className={`${btn} border border-red-300 text-red-700 hover:bg-red-50`}
            disabled={busy}
            onClick={() =>
              run(() =>
                api.post(
                  `${base}/cancel-acquisition`,
                  stopReason.trim() ? { reason: stopReason } : {},
                ),
              )
            }
          >
            {busy ? "Working..." : "Stop acquisition"}
          </button>
        </div>
        <p className="text-xs text-gray-500">
          Stopping ends this attempt and records what was established so far. It says nothing about
          the paper, and the study can be resumed.
        </p>
      </div>
    );
  } else if (RESUMABLE_STATES.includes(study.state) && canApprove) {
    // change_7.3 section 10 item 10: the helper text is generated from THIS study's limitations and
    // says what would unblock each one. The fixed text said credentials might suffice, which is
    // false for an archive bioAF has no adapter for.
    const requirements = study.resume?.requirements ?? [];
    controls = (
      <div className="space-y-2">
        <button
          className={`${btn} border border-gray-300 text-gray-800 hover:bg-gray-50`}
          disabled={busy}
          onClick={() => run(() => api.post(`${base}/resume`, {}))}
        >
          {busy ? "Working..." : (study.resume?.label ?? "Review and resume")}
        </button>
        {requirements.length > 0 && (
          <ul className="list-disc pl-5 text-xs text-gray-600">
            {requirements.map((requirement) => (
              <li key={requirement}>{requirement}</li>
            ))}
          </ul>
        )}
        <p className="text-xs text-gray-500">
          {requirements.length === 0 &&
            "Credentials, a corrected accession or a newly public deposit can make a blocked study runnable. "}
          Resuming returns it to the approval gate so you decide again, rather than starting a run. What
          was already established is kept.
        </p>
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
