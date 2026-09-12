"use client";

/**
 * change_7.5 sections 2.2, 2.5 and 2.6: what this run checks, and why.
 *
 * Study 38 chose nf-core/chipseq from one paper-level method before any claim, then found both of
 * its RNA-seq contrasts incompatible and ran nothing, while every differential count read "No
 * supported metric" beside the authors' own result tables. Here each claim shows its experiment and
 * all four checks, each on its own; the run's claim and check are shown with the workflow that
 * followed from them, who decided and why; every other claim is "Not assessed in this run" with the
 * reason; and anything computed for an earlier selection is shown as history, never as current.
 *
 * Rendered from the report projection. The labels are the backend's, pending the owner's sign-off.
 */

import type { ClaimCheck, ReportSummary } from "@/lib/validationReport";

const STATUS_CLASS: Record<string, string> = {
  available: "bg-emerald-50 text-emerald-700",
  unavailable: "bg-gray-100 text-gray-600",
  unresolved: "bg-amber-50 text-amber-700",
  usable: "bg-emerald-50 text-emerald-700",
  unstated: "bg-amber-50 text-amber-700",
};

function Status({ status, label }: { status: string | null; label: string | null }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_CLASS[status ?? ""] ?? "bg-gray-100 text-gray-600"}`}>
      {label ?? status ?? "Not established"}
    </span>
  );
}

function CheckRow({ check }: { check: ClaimCheck }) {
  return (
    <li className="flex flex-wrap items-baseline gap-2 text-xs">
      <span className="w-64 shrink-0 text-gray-700">{check.label}</span>
      <Status status={check.status} label={check.status_label} />
      {check.reason && <span className="text-gray-500">{check.reason}</span>}
    </li>
  );
}

const DECIDED_BY: Record<string, string> = {
  model: "chosen by the model",
  only_candidate: "the only claim a run on this route can check",
  ranking: "chosen by ranking, because the model's answer was not one of the candidates",
  proposal: "proposed for a person to confirm at the gate",
  human: "chosen by a person at the gate",
};

export function ClaimSelection({ summary }: { summary: ReportSummary | null | undefined }) {
  const experiments = summary?.experiments ?? [];
  const selection = summary?.selection ?? null;
  const claims = (summary?.claims ?? []).filter((claim) => (claim.checks ?? []).length > 0 || claim.selection);
  const history = summary?.selection_history ?? [];
  if (experiments.length === 0 && !selection && claims.length === 0) return null;

  return (
    <div className="space-y-4">
      {selection && (
        <div data-testid="claim-selection-current" className="rounded border border-gray-200 bg-gray-50 p-3 text-sm">
          {selection.check_label ? (
            <p>
              This run checks claim {(selection.claim_index ?? 0) + 1} by {selection.check_label}
              {selection.workflow ? ` on ${selection.workflow}` : ""}
              {selection.experiment_id ? ` (experiment ${selection.experiment_id})` : ""}, revision {selection.revision}:{" "}
              {DECIDED_BY[selection.decided_by ?? ""] ?? selection.decided_by}.
            </p>
          ) : (
            <p>No claim is checked by this run.</p>
          )}
          {selection.reason && <p className="mt-1 text-xs text-gray-600">{selection.reason}</p>}
          {selection.superseded_revisions > 0 && (
            <p className="mt-1 text-xs text-gray-500">
              {selection.superseded_revisions} earlier {selection.superseded_revisions === 1 ? "selection" : "selections"} kept as
              history.
            </p>
          )}
        </div>
      )}

      {experiments.length > 0 && (
        <div className="space-y-2">
          {experiments.map((experiment) => (
            <div key={experiment.id ?? ""} data-testid={`experiment-${experiment.id}`} className="text-sm">
              <p className="font-medium text-gray-800">
                <span className="font-mono text-xs">{experiment.id}</span> {experiment.assay ?? "Assay not stated"}
                <span className="ml-2 text-xs font-normal text-gray-500">{experiment.workflow ?? "No workflow"}</span>
              </p>
              <ul className="mt-1 space-y-0.5">
                {experiment.reference.map((part) => (
                  <li key={part.part} className="flex flex-wrap items-baseline gap-2 text-xs">
                    <span className="w-24 shrink-0 capitalize text-gray-600">{part.part}</span>
                    <span className="text-gray-800">{part.stated ?? part.resolved ?? "Not stated"}</span>
                    <Status status={part.status} label={part.status_label} />
                    {part.established_from && <span className="text-gray-500">established from the {part.established_from}</span>}
                    {part.reason && <span className="text-gray-500">{part.reason}</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {claims.length > 0 && (
        <ol className="space-y-3">
          {summary?.claims.map((claim, index) =>
            (claim.checks ?? []).length > 0 || claim.selection ? (
              <li key={index} data-testid={`claim-${index}`} className="text-sm">
                <p className="text-gray-800">{claim.description}</p>
                <p className="text-xs text-gray-500">
                  {claim.experiment ? `Experiment ${claim.experiment.id}${claim.experiment.assay ? ` (${claim.experiment.assay})` : ""}` : "Not linked to an experiment"}
                  {claim.contrast ? `; ${claim.contrast}` : ""}
                  {claim.cutoff ? `; ${claim.cutoff}` : ""}
                </p>
                {claim.selection && (
                  <p className="mt-1 text-xs">
                    <span className={claim.selection.status === "selected" ? "font-medium text-emerald-700" : "text-gray-600"}>
                      {claim.selection.label}
                    </span>
                    {claim.selection.check_label && <span className="text-gray-600">: {claim.selection.check_label}</span>}
                    {claim.selection.status === "unassessed" && claim.selection.reason && (
                      <span className="text-gray-500"> ({claim.selection.reason})</span>
                    )}
                  </p>
                )}
                <ul className="mt-1 space-y-0.5">
                  {(claim.checks ?? []).map((check) => (
                    <CheckRow key={check.key} check={check} />
                  ))}
                </ul>
              </li>
            ) : null,
          )}
        </ol>
      )}

      {history.length > 0 && (
        <ul className="text-xs text-gray-500">
          {history.map((entry, i) => (
            <li key={i}>
              {entry.label}: {entry.artifacts.join(", ")}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
