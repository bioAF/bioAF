"use client";

/**
 * plan_7 step 19, part 2: what the GEO metadata led us to expect, against what we actually saw.
 *
 * `ValidationEvidenceTable` already renders the paper-CLAIMED against our-COMPUTED metrics, with
 * verdict chips, unit reconciliation, tolerance and the advisory flag. It is EXTENDED here, not
 * rebuilt: this is the second comparison the DESIRED STATE asks for and that table has no home for,
 * namely the species, the sample count and the condition structure the deposit itself declared,
 * against what the acquired data turned out to be.
 */

import type { PrecomputeCheck } from "./PrecomputeChecksPanel";
import type { DepositAssociation, DepositInspection } from "./DepositPanel";

export interface ExpectedEvidence {
  precompute_checks?: {
    species_matches?: PrecomputeCheck | null;
    sample_data_matches_paper?: PrecomputeCheck | null;
  } | null;
  deposit_inspection?: DepositInspection | null;
  deposit_metadata_association?: DepositAssociation[] | null;
}

const SOURCE_LABEL: Record<string, string> = {
  metadata_file: "metadata file",
  series_matrix: "GEO series matrix",
  column_name: "column name",
  unresolved: "unresolved",
};

export function ExpectedVsObserved({ evidence }: { evidence: ExpectedEvidence }) {
  const checks = evidence.precompute_checks;
  const inspection = evidence.deposit_inspection;
  const association = evidence.deposit_metadata_association;

  if (!checks && !inspection && !association) return null;

  const rows: { label: string; value: string }[] = [];
  if (checks?.species_matches?.detail) {
    rows.push({ label: "Species", value: checks.species_matches.detail });
  }
  if (checks?.sample_data_matches_paper?.detail) {
    rows.push({ label: "Sample count", value: checks.sample_data_matches_paper.detail });
  }
  if (inspection) {
    rows.push({
      label: "The matrix we used",
      value: `${inspection.n_rows.toLocaleString()} rows, ${inspection.n_columns} sample column${
        inspection.n_columns === 1 ? "" : "s"
      }, values measured as ${inspection.value_type_observed}`,
    });
  }

  return (
    <div className="space-y-3 text-sm">
      {rows.length > 0 && (
        <table className="min-w-full">
          <tbody className="divide-y divide-gray-100">
            {rows.map((row) => (
              <tr key={row.label}>
                <td className="py-1.5 pr-4 align-top text-gray-500">{row.label}</td>
                <td className="py-1.5 text-gray-800">{row.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {association && association.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Condition structure, and where each column&apos;s condition came from
          </p>
          <table className="mt-1 min-w-full text-xs">
            <tbody className="divide-y divide-gray-100">
              {association.map((row) => (
                <tr key={row.column}>
                  <td className="py-1 pr-3 font-mono text-gray-800">{row.column}</td>
                  <td className="py-1 pr-3 text-gray-700">{row.condition || "unresolved"}</td>
                  <td className="py-1 pr-3 text-gray-500">{SOURCE_LABEL[row.source] ?? row.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
