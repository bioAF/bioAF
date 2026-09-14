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

import type { ClaimCheck, ClaimConsistency, ClaimList, ReportClaim, ReportSummary } from "@/lib/validationReport";

import { TableConfirmation } from "./TableConfirmation";

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

// plan_8_2 section 3.2: what a count of the authors' published list counted, the passage naming the file as
// the list, and what the count does not check.
function ListCount({ consistency, list }: { consistency: ClaimConsistency; list: ClaimList }) {
  const subgroup = list.subgroup?.definition
    ? ` ${list.subgroup.definition}${list.subgroup.field ? ` (field ${list.subgroup.field})` : ""}`
    : "";
  const missing = consistency.rows_missing
    ? `; ${consistency.rows_missing} ${consistency.rows_missing === 1 ? "row" : "rows"} with no identifier excluded`
    : "";
  return (
    <div data-testid="consistency-list" className="text-gray-500">
      {consistency.rows_passing !== null && consistency.rows_passing !== undefined && (
        <p>
          {consistency.rows_passing} distinct identifiers{subgroup} among {consistency.rows_tested} rows{missing}
        </p>
      )}
      {list.evidence?.text && <p>The paper names this file as the list: &quot;{list.evidence.text}&quot;</p>}
      {consistency.assumptions.map((assumption) => (
        <p key={assumption}>{assumption}</p>
      ))}
    </div>
  );
}

const CONSISTENCY_CLASS: Record<string, string> = {
  agree: "font-medium text-emerald-700",
  disagree: "font-medium text-red-700",
  unresolved: "text-amber-700",
  not_checkable: "text-gray-600",
  pending_re_evaluation: "text-gray-600",
};

const DECIDED_BY: Record<string, string> = {
  model: "chosen by the model",
  only_candidate: "the only claim a run on this route can check",
  ranking: "chosen by ranking, because the model's answer was not one of the candidates",
  proposal: "proposed for a person to confirm at the gate",
  human: "chosen by a person at the gate",
};

// plan_8_2 section 4.2: one claim, with its experiment, predicate, checks, consistency and result. The Findings
// section nests it under its finding; the anchor `claim-{index}` is the same wherever it is rendered.
export function ClaimItem({
  claim,
  index,
  studyId,
  onChanged,
}: {
  claim: ReportClaim;
  index: number;
  studyId?: number;
  onChanged?: (updated: unknown) => void;
}) {
  return (
    <li id={`claim-${index}`} data-testid={`claim-${index}`} className="text-sm">
      <p className="text-gray-800">{claim.description}</p>
      <p className="text-xs text-gray-500">
        {claim.experiment ? `Experiment ${claim.experiment.id}${claim.experiment.assay ? ` (${claim.experiment.assay})` : ""}` : "Not linked to an experiment"}
        {claim.contrast ? `; ${claim.contrast}` : ""}
        {claim.cutoff ? `; ${claim.cutoff}` : ""}
      </p>
      {claim.predicate && <p className="text-xs text-gray-600">{claim.predicate}</p>}
      {claim.cutoff_source?.kind === "methods" && claim.cutoff_source.quote && (
        <p data-testid="cutoff-source" className="text-xs text-gray-500">
          Cutoff from the methods: &quot;{claim.cutoff_source.quote}&quot;
        </p>
      )}
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
      <ul className="mt-1 space-y-0.5" data-testid="claim-checks">
        {(claim.checks ?? []).map((check) => (
          <CheckRow key={check.key} check={check} />
        ))}
      </ul>
      {claim.consistency && (
        <div className="mt-1 text-xs">
          <span className={CONSISTENCY_CLASS[claim.consistency.outcome ?? ""] ?? "text-gray-700"}>
            {claim.consistency.label ??
              (claim.consistency.check_state_label
                ? `Consistency with the authors' results: ${claim.consistency.check_state_label}`
                : null)}
          </span>
          {claim.consistency.table && <span className="text-gray-500"> ({claim.consistency.table})</span>}
          {claim.consistency.method !== "published_list_count" &&
            claim.consistency.rows_passing !== null &&
            claim.consistency.rows_passing !== undefined && (
              <span className="text-gray-500">
                {" "}
                {claim.consistency.rows_passing} of {claim.consistency.rows_tested} rows pass
              </span>
            )}
          {claim.consistency.reason && <p className="text-gray-500">{claim.consistency.reason}</p>}
          {claim.consistency.method === "published_list_count" && claim.consistency.list && (
            <ListCount consistency={claim.consistency} list={claim.consistency.list} />
          )}
          {claim.consistency.interpretation?.source === "confirmation" && (
            <p data-testid="consistency-interpretation" className="text-gray-500">
              Table read as {claim.consistency.interpretation.evidence.join("; ")}
            </p>
          )}
          {studyId !== undefined &&
            onChanged &&
            claim.consistency.outcome === "unresolved" &&
            claim.consistency.table &&
            claim.contrast && (
              <TableConfirmation
                studyId={studyId}
                table={claim.consistency.table}
                contrast={claim.contrast}
                columnsCount={claim.consistency.columns_count}
                candidateRoles={claim.consistency.candidate_roles}
                onChanged={onChanged}
              />
            )}
          {claim.consistency.superseded && (
            <p data-testid="consistency-superseded" className="text-gray-500">
              Superseded comparison, not current evidence: {claim.consistency.superseded.label}
              {claim.consistency.superseded.reason ? ` (${claim.consistency.superseded.reason})` : ""}
            </p>
          )}
          {claim.consistency.candidates.length > 0 && (
            <ul className="ml-4 list-disc text-gray-500">
              {claim.consistency.candidates.map((candidate, i) => (
                <li key={i}>
                  {candidate.interpretation}: {candidate.count ?? "not counted"}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {claim.result && (
        <p className="mt-1 text-xs text-gray-700">
          {claim.result.tier}: {claim.result.verdict ?? "no concordance"}
          {claim.result.count && (
            <span className="text-gray-500">
              ; {claim.result.count.label}: {claim.result.count.words}
            </span>
          )}
        </p>
      )}
    </li>
  );
}

type ClaimSelectionPart = "selection" | "experiments" | "claims" | "history";
const ALL_PARTS: ClaimSelectionPart[] = ["selection", "experiments", "claims", "history"];

export function ClaimSelection({
  summary,
  studyId,
  onChanged,
  parts = ALL_PARTS,
}: {
  summary: ReportSummary | null | undefined;
  // plan_8_2 section 3.1: given, an unresolved check against a table offers the recorded confirmation.
  studyId?: number;
  onChanged?: (updated: unknown) => void;
  // plan_8_2 section 4.2: which parts to render. The report shows the selection and experiments under
  // Checks performed, the claims under their findings, and the history under Run diagnostics.
  parts?: ClaimSelectionPart[];
}) {
  const experiments = parts.includes("experiments") ? (summary?.experiments ?? []) : [];
  const selection = parts.includes("selection") ? (summary?.selection ?? null) : null;
  const claims = parts.includes("claims")
    ? (summary?.claims ?? []).filter((claim) => (claim.checks ?? []).length > 0 || claim.selection)
    : [];
  const history = parts.includes("history") ? (summary?.selection_history ?? []) : [];
  if (experiments.length === 0 && !selection && claims.length === 0 && history.length === 0) return null;

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
              <ClaimItem key={index} claim={claim} index={index} studyId={studyId} onChanged={onChanged} />
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
