"use client";

/**
 * change_7.1 section 2: the paper's own attachments, and what each one turned out to hold.
 *
 * There was no supplement discovery. Code availability came from what a model read in the prose,
 * the deposit inventory listed GEO and nothing else, and an article's attachments were never looked
 * at. Groff et al. lost a 54-row sample metadata table, the authors' complete R analysis and the
 * differential-results table, all public the whole time.
 *
 * **An unresolved reference renders as unresolved.** "bioAF could not fetch it" and "the authors
 * did not publish it" are different statements, and only the second is a finding about the paper.
 *
 * **The counts are shown because they separate claims.** A paper stating "194 genes reached
 * significance, 88 of which had |log2FC| > 2" makes two claims in one sentence, and a reader
 * checking either one needs both numbers.
 *
 * change_7.3 sections 10 and 11: rendered from the report projection. Each attachment appears once
 * with its retrieval status; one bundle failure is ONE notice listing what it affected, with the
 * technical detail collapsed beneath it. Study 34 showed the same 404 on every row.
 */

import type { ReportSummary } from "@/lib/validationReport";
import { TechnicalDetails } from "./TechnicalDetails";

export interface Supplement {
  label: string;
  filename: string | null;
  role: string;
  resolved: boolean;
  row_count?: number | null;
  columns: string[] | null;
  threshold_splits: Record<string, number> | null;
  size_bytes: number | null;
  failure_reason: string | null;
}

const ROLE_LABEL: Record<string, string> = {
  sample_metadata: "Sample metadata",
  expression_matrix: "Expression matrix",
  results_table: "Differential results",
  code: "Analysis code",
  supporting_input: "Supporting input",
  unknown: "Not established",
};

function detailOf(supplement: Supplement): string {
  const parts: string[] = [];
  // A missing key is not a count: `row_count !== null` was true for an absent field and rendered
  // "undefined rows" on every document and every file nobody retrieved.
  if (typeof supplement.row_count === "number") parts.push(`${supplement.row_count} rows`);
  for (const [name, count] of Object.entries(supplement.threshold_splits ?? {})) {
    parts.push(`${count} with ${name}`);
  }
  if (supplement.failure_reason) parts.push(supplement.failure_reason);
  return parts.join("; ") || supplement.filename || "";
}

const RETRIEVAL_CLASS: Record<string, string> = {
  retrieved: "bg-emerald-50 text-emerald-700",
  failed: "bg-amber-50 text-amber-700",
  not_in_bundle: "bg-amber-50 text-amber-700",
  not_attempted: "bg-gray-100 text-gray-600",
};

function ProjectedInventory({ summary }: { summary: ReportSummary }) {
  const packaging = [
    summary.index_pages ? `${summary.index_pages} index page${summary.index_pages === 1 ? "" : "s"}` : null,
    summary.figures ? `${summary.figures} figure image${summary.figures === 1 ? "" : "s"}` : null,
  ].filter(Boolean);
  return (
    <div className="space-y-3">
      {summary.retrieval_failures.map((failure, i) => (
        <div
          key={i}
          data-testid="retrieval-failure"
          className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          <p>{failure.message}.</p>
          <p className="mt-0.5 text-xs">Affected: {failure.artifacts.join(", ")}</p>
          <TechnicalDetails detail={failure.technical_detail} />
        </div>
      ))}
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <tbody className="divide-y divide-gray-100">
            {summary.artifacts.map((artifact) => (
              <tr key={artifact.identity ?? artifact.label} data-testid={`artifact-${artifact.identity}`}>
                <td className="py-1.5 pr-4 text-gray-700">{artifact.label}</td>
                <td className="py-1.5 pr-4">
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                      RETRIEVAL_CLASS[artifact.retrieval.status] ?? "bg-gray-100 text-gray-600"
                    }`}
                  >
                    {artifact.retrieval.label}
                  </span>
                </td>
                <td className="py-1.5 pr-4 text-xs text-gray-600">{artifact.inspection.role_label ?? ""}</td>
                <td className="py-1.5 pr-4 text-xs text-gray-500">{artifact.inspection.measurements.join("; ")}</td>
                <td className="py-1.5 text-xs text-gray-500">
                  {artifact.identification_label && <>Named in {artifact.identification_label}</>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {packaging.length > 0 && (
        <p className="text-xs text-gray-500">The bundle&apos;s packaging, not counted as attachments: {packaging.join(", ")}.</p>
      )}
    </div>
  );
}

export function SupplementInventory({
  supplements,
  summary,
}: {
  supplements: Supplement[] | null | undefined;
  summary?: ReportSummary | null;
}) {
  if (summary && (summary.artifacts.length > 0 || summary.retrieval_failures.length > 0)) {
    return <ProjectedInventory summary={summary} />;
  }
  if (!supplements || supplements.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <tbody className="divide-y divide-gray-100">
          {supplements.map((supplement) => (
            <tr key={supplement.label} data-testid={`supplement-${supplement.label}`}>
              <td className="py-1.5 pr-4 text-gray-700">{supplement.label}</td>
              <td className="py-1.5 pr-4">
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                    supplement.resolved ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"
                  }`}
                >
                  {supplement.resolved ? (ROLE_LABEL[supplement.role] ?? supplement.role) : "Not retrieved"}
                </span>
              </td>
              <td className="py-1.5 text-xs text-gray-500">{detailOf(supplement)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
