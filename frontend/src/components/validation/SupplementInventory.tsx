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
 */

export interface Supplement {
  label: string;
  filename: string | null;
  role: string;
  resolved: boolean;
  row_count: number | null;
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
  if (supplement.row_count !== null) parts.push(`${supplement.row_count} rows`);
  for (const [name, count] of Object.entries(supplement.threshold_splits ?? {})) {
    parts.push(`${count} with ${name}`);
  }
  if (supplement.failure_reason) parts.push(supplement.failure_reason);
  return parts.join("; ") || supplement.filename || "";
}

export function SupplementInventory({ supplements }: { supplements: Supplement[] | null | undefined }) {
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
