"use client";

/**
 * change_7.1 section 7: what could and could not be established.
 *
 * Study 32 showed a single sentence, phrased as a claim about the paper rather than about the
 * deposit, with no sign of the checks that ran against the paper's own attachments and no sign
 * that more than one thing was blocking at once.
 *
 * **Every limitation names the resource and the route it affects.** "Nothing could run" tells a
 * reader nothing they can act on; "EGAS00001003667 is controlled access, so the raw-read route
 * needs a data access agreement" tells them exactly what to do next.
 *
 * **Published results and a reproduction input are separate rows.** A differential-results table
 * proves the authors published processed results; only a sample-level matrix is something to
 * reproduce from.
 */

import type { ReportSummary } from "@/lib/validationReport";
import { TechnicalDetails } from "./TechnicalDetails";

export interface Limitation {
  kind: string;
  resource: string | null;
  operation: string | null;
  detail: string | null;
}

export interface Completion {
  classification: string;
  reason: string;
  limitations: Limitation[];
  processed_results_available: boolean;
  reproduction_input_available: boolean;
  checks_completed: string[];
  checks_not_completed: string[];
}

const LIMITATION_LABEL: Record<string, string> = {
  controlled_access: "Controlled access",
  unsupported_acquisition: "Acquisition not supported",
  missing_input: "Required input not published",
  failed_discovery: "Could not be established",
};

function Fact({ testId, label, value }: { testId: string; label: string; value: boolean }) {
  return (
    <div className="flex items-center gap-2" data-testid={testId}>
      <span className="text-gray-700">{label}</span>
      <span
        className={`rounded px-1.5 py-0.5 text-xs font-medium ${
          value ? "bg-emerald-50 text-emerald-700" : "bg-gray-100 text-gray-600"
        }`}
      >
        {value ? "Yes" : "No"}
      </span>
    </div>
  );
}

const TRISTATE_CLASS: Record<string, string> = {
  yes: "bg-emerald-50 text-emerald-700",
  no: "bg-gray-100 text-gray-600",
  not_established: "bg-amber-50 text-amber-700",
  unknown: "bg-amber-50 text-amber-700",
};

/**
 * change_7.3 sections 10 and 11: the same statements, from the report projection.
 *
 * The two facts are tri-state: a failed download is "Not established", never "No". The limitation
 * label applies only when an absence is established, and a route leg that was not chosen is reported
 * as context. A failure affecting several attachments is one line, not one per attachment.
 */
function ProjectedCompletion({ summary }: { summary: ReportSummary }) {
  return (
    <div className="space-y-3 text-sm">
      <div className="space-y-1">
        {summary.completion_facts.map((fact) => (
          <div key={fact.key} className="flex flex-wrap items-baseline gap-x-2" data-testid={`fact-${fact.key}`}>
            <span className="text-gray-700">{fact.label}</span>
            <span
              className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                TRISTATE_CLASS[fact.value] ?? "bg-gray-100 text-gray-600"
              }`}
            >
              {fact.value_label}
            </span>
            {fact.reason && <span className="text-xs text-gray-500">{fact.reason}</span>}
          </div>
        ))}
      </div>

      {summary.limitations.length > 0 && (
        <table className="min-w-full">
          <tbody className="divide-y divide-gray-100">
            {summary.limitations.map((limitation, i) => (
              <tr
                key={`${limitation.kind}-${limitation.leg}-${i}`}
                data-testid={limitation.governs ? `limitation-${limitation.kind}` : `limitation-context-${limitation.leg}`}
              >
                <td className="py-1.5 pr-4 text-gray-700">
                  {limitation.label}
                  {!limitation.governs && (
                    <span className="ml-1 text-xs text-gray-500">(route not chosen; reported as context)</span>
                  )}
                </td>
                <td className="py-1.5 pr-4 text-xs text-gray-500">
                  {limitation.resource}
                  {limitation.leg && <span className="ml-2">{limitation.leg} route</span>}
                </td>
                <td className="py-1.5 text-xs text-gray-500">
                  {limitation.detail}
                  {limitation.observation && <span className="block">Observed: {limitation.observation}</span>}
                  <TechnicalDetails detail={limitation.technical_detail} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {[
        { heading: "Checks completed", rows: summary.checks_completed },
        { heading: "Checks not completed", rows: summary.checks_not_completed },
      ]
        .filter(({ rows }) => rows?.length > 0)
        .map(({ heading, rows }) => (
          <div key={heading}>
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">{heading}</p>
            <ul className="mt-1 list-disc pl-5 text-xs text-gray-600">
              {rows.map((row) => (
                <li key={row}>{row}</li>
              ))}
            </ul>
          </div>
        ))}
    </div>
  );
}

export function CompletionSummary({
  completion,
  summary,
}: {
  completion: Completion | null | undefined;
  summary?: ReportSummary | null;
}) {
  if (summary && summary.completion_facts.length > 0) return <ProjectedCompletion summary={summary} />;
  if (!completion) return null;

  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap gap-4">
        <Fact
          testId="fact-processed-results"
          label="Processed results published"
          value={completion.processed_results_available}
        />
        <Fact
          testId="fact-reproduction-input"
          label="Reproduction input available"
          value={completion.reproduction_input_available}
        />
      </div>

      {completion.limitations?.length > 0 && (
        <table className="min-w-full">
          <tbody className="divide-y divide-gray-100">
            {completion.limitations.map((limitation) => (
              <tr key={`${limitation.kind}-${limitation.operation}`} data-testid={`limitation-${limitation.kind}`}>
                <td className="py-1.5 pr-4 text-gray-700">
                  {LIMITATION_LABEL[limitation.kind] ?? limitation.kind}
                </td>
                <td className="py-1.5 pr-4 text-xs text-gray-500">
                  {limitation.resource}
                  {limitation.operation && <span className="ml-2">{limitation.operation} route</span>}
                </td>
                <td className="py-1.5 text-xs text-gray-500">{limitation.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {[
        { heading: "Checks completed", rows: completion.checks_completed },
        { heading: "Checks not completed", rows: completion.checks_not_completed },
      ]
        .filter(({ rows }) => rows?.length > 0)
        .map(({ heading, rows }) => (
          <div key={heading}>
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">{heading}</p>
            <ul className="mt-1 list-disc pl-5 text-xs text-gray-600">
              {rows.map((row) => (
                <li key={row}>{row}</li>
              ))}
            </ul>
          </div>
        ))}
    </div>
  );
}
