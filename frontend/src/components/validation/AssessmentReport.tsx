"use client";

import { Card } from "@/components/ui/Card";
import type {
  AreaStatement,
  AssessmentSummary,
  CodeFollowup,
  ReportArea,
  ReportSynthesis,
} from "@/lib/validationReport";

/**
 * plan_8_7 stage 2: one summary leads, six areas expand, the numbers sit behind them.
 *
 * The report opened on two prominent scorecards with different semantics, and then four engineering
 * sections: Findings, Data and code, Checks performed, Run diagnostics. A scientist deciding whether
 * to rely on a paper has six questions, and section 3 of the plan names them.
 *
 * Three rules this surface holds to:
 *
 * - **an area shows supported observations, concerns and untested questions together.** There is no
 *   per-area verdict badge, because "do not force a whole section into one pass/fail label".
 * - **the summary carries no number.** The numerical rubric is secondary, expandable, and elsewhere.
 * - **untested is untested.** A question bioAF could not settle says what would settle it, and never
 *   reads as something the authors omitted.
 *
 * Every sentence here is the backend's. This file decides nothing about the paper.
 */

const CONCERN = "border-l-2 border-red-300 pl-3";
const SUPPORTED = "border-l-2 border-emerald-300 pl-3";
const UNTESTED = "border-l-2 border-gray-300 pl-3";

function Statement({ row, tone }: { row: AreaStatement; tone: string }) {
  return (
    <li className={`${tone} space-y-0.5`}>
      <p className="text-sm text-gray-800">{row.statement}</p>
      {row.scope && <p className="text-xs text-gray-500">Where: {row.scope}</p>}
      {row.inferential_step && (
        <p className="text-xs text-gray-600">The step at issue: {row.inferential_step}</p>
      )}
      {row.impact && <p className="text-xs text-gray-600">What it costs: {row.impact}</p>}
      {row.next_action && <p className="text-xs text-gray-600">What would settle it: {row.next_action}</p>}
      {row.citations && row.citations.length > 0 && (
        <p className="text-xs text-gray-500">
          Cites {row.citations.length} source {row.citations.length === 1 ? "passage" : "passages"}:{" "}
          {row.citations.slice(0, 6).map((citation) => (
            <a key={citation} href={`#evidence-${citation}`} className="mr-1 font-mono underline hover:text-gray-700">
              {citation}
            </a>
          ))}
        </p>
      )}
    </li>
  );
}

function Group({ label, rows, tone }: { label: string; rows: AreaStatement[]; tone: string }) {
  if (rows.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">{label}</h4>
      <ul className="space-y-2">
        {rows.map((row) => (
          <Statement key={row.leaf} row={row} tone={tone} />
        ))}
      </ul>
    </div>
  );
}

/** What a run established, apart from the documentary account. An unresolved comparison is not an agreement. */
function ReproductionDepth({ area }: { area: ReportArea }) {
  const depth = area.reproduction;
  if (!depth) return null;
  const line = !depth.attempted
    ? `No reproduction of this paper's results has been attempted${depth.reason ? `: ${depth.reason}` : "."}`
    : !depth.performed
      ? "Execution was attempted; no comparison with the paper's results has been established."
    : depth.unresolved
      ? "A comparison ran and did not resolve, so bioAF has established neither agreement nor disagreement."
      : depth.agreed
        ? "The comparisons bioAF ran agreed with the paper within the contract declared before the run."
        : "The comparisons bioAF ran did not agree with the paper.";
  return (
    <div className="space-y-1" data-testid="reproduction-depth">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Reproduction depth</h4>
      <p className="text-sm text-gray-800">{line}</p>
      {depth.comparisons.length > 0 && (
        <ul className="space-y-0.5 text-xs text-gray-600">
          {depth.comparisons.map((row, i) => (
            <li key={i}>
              {row.metric ?? "the reported result"}: the paper reports {String(row.paper)}, bioAF&apos;s run
              produced {String(row.ours)}
              {row.agrees === null || row.agrees === undefined ? " (unresolved)" : row.agrees ? " (agrees)" : " (differs)"}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const FOLLOWUP_LABEL: Record<CodeFollowup["action"], string> = {
  attempt_reproduction: "Reproduction required",
  attempt_bounded_check: "Author-code invocation required",
  needs_authorization: "Not run: needs authorisation",
  blocked: "Blocked",
};

/**
 * plan_8_7 section 3: what attempting each published implementation requires. Silence here would read
 * as a paper that published no code, which is a different and false statement.
 */
function CodeFollowupRows({ area }: { area: ReportArea }) {
  const rows = area.followup ?? [];
  const blocked = area.followup_blocked ?? [];
  if (rows.length === 0 && blocked.length === 0) return null;
  return (
    <div className="space-y-1.5" data-testid="code-followup">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        Attempting the authors&apos; code
      </h4>
      <ul className="space-y-2">
        {rows.map((row) => (
          <li key={row.unit} className="border-l-2 border-gray-300 pl-3">
            <p className="text-sm text-gray-800">
              <span className="font-mono text-xs">{(row.paths ?? [row.unit]).join(", ")}</span>
              {" - "}
              {row.status === "running" && row.session_id ? "Author code running"
                : row.status === "settled" ? (row.outcome?.reproduced ? "Results reproduced" : "Attempt completed")
                : row.status === "blocked" ? "Blocked" : FOLLOWUP_LABEL[row.action]}
              {row.attempted ? " (attempted)" : ""}
            </p>
            <p className="text-xs text-gray-600">{row.reason}</p>
            {row.status === "settled" && !row.outcome?.reproduced && (
              <p className="text-xs text-gray-600">Agreement with the paper&apos;s results has not been established.</p>
            )}
            {row.next_action && <p className="text-xs text-gray-600">What would settle it: {row.next_action}</p>}
            {row.source?.commit_sha && (
              <p className="text-xs text-gray-500">
                At revision <span className="font-mono">{row.source.commit_sha.slice(0, 12)}</span>
              </p>
            )}
          </li>
        ))}
      </ul>
      {blocked.map((row, i) => (
        <p key={i} className="text-xs text-amber-800">
          bioAF could not start this: {row.reason}
        </p>
      ))}
    </div>
  );
}

function Area({ area }: { area: ReportArea }) {
  const total = area.supported.length + area.concerns.length + area.untested.length;
  return (
    <details className="border-t border-gray-100 py-3" open={area.concerns.length > 0}>
      <summary className="cursor-pointer list-none">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-sm font-semibold text-gray-900">{area.title}</span>
          <span className="text-sm text-gray-600">{area.summary}</span>
        </div>
        <p className="mt-0.5 text-xs text-gray-500">{area.question}</p>
      </summary>
      <div className="mt-3 space-y-3 pl-1">
        {area.key === "results" && <ReproductionDepth area={area} />}
        {area.key === "code" && <CodeFollowupRows area={area} />}
        <Group label="Supported" rows={area.supported} tone={SUPPORTED} />
        <Group label="Concerns bioAF demonstrated" rows={area.concerns} tone={CONCERN} />
        <Group label="Questions bioAF could not settle" rows={area.untested} tone={UNTESTED} />
        {total === 0 && area.key !== "results" && (
          <p className="text-sm text-gray-600">There is nothing recorded for this area yet.</p>
        )}
        {area.scope && <p className="text-xs text-gray-500">Scope: {area.scope}</p>}
      </div>
    </details>
  );
}

export function AssessmentReport({
  areas,
  summary,
  synthesis,
}: {
  areas: ReportArea[];
  summary: AssessmentSummary;
  synthesis: ReportSynthesis | null;
}) {
  const lead = synthesis?.lead ?? factualLead(summary);
  const counts = summary.counts;
  return (
    <Card title="What bioAF established about this paper" padding="sm">
      <div className="space-y-3">
        <p className="text-sm text-gray-800">{lead}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="assessment-counts">
          <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">
            {counts.obligations_attempted} checks attempted
          </span>
          <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">
            {counts.obligations_conclusive} checks conclusive
          </span>
          <span className="rounded bg-red-50 px-1.5 py-0.5 text-red-700">
            {counts.findings_conclusive} findings conclusive
          </span>
          {counts.conclusions_reviewed > 0 && (
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">
              {counts.conclusions_reviewed} conclusions reviewed
            </span>
          )}
          {summary.assessment_revision !== null && (
            <span className="text-gray-500">
              Revision {summary.assessment_revision}
              {synthesis?.method === "model_assisted" ? ", summary written by the configured model" : ""}
            </span>
          )}
        </div>
        {summary.reason && <p className="text-sm text-gray-600">{summary.reason}</p>}
        <div>{areas.map((area) => <Area key={area.key} area={area} />)}</div>
      </div>
    </Card>
  );
}

/**
 * The backend writes this sentence too, and sends it as the synthesis whenever a model one was not
 * produced. It is repeated here for the case where a report carries areas and no synthesis at all,
 * which is a projection made before the synthesis was stored.
 */
function factualLead(summary: AssessmentSummary): string {
  if (summary.reason) return summary.reason;
  const parts: string[] = [];
  if (summary.concern_count > 0) {
    parts.push(
      `${summary.concern_count} demonstrated concern${summary.concern_count === 1 ? "" : "s"}, the most consequential being: ${summary.concerns[0]?.statement ?? ""}`,
    );
  }
  if (summary.supported_count > 0) {
    parts.push(
      `${summary.supported_count} supported observation${summary.supported_count === 1 ? "" : "s"}, including: ${summary.supported[0]?.statement ?? ""}`,
    );
  }
  if (summary.untested_count > 0) {
    parts.push(
      `${summary.untested_count} question${summary.untested_count === 1 ? "" : "s"} bioAF could not settle.`,
    );
  }
  if (!summary.reproduction.attempted) {
    parts.push("No independent reproduction of this paper's results has been attempted.");
  }
  return parts.join(" ") || "bioAF established nothing about this paper.";
}
