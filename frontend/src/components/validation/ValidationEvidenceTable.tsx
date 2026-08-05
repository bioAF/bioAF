// The computed-vs-claimed evidence a scientist reads at the `comparing` gate. Once the automatic
// classifier (E2/E3/E4) has run, evidence.classification_result.comparisons is the AUTHORITATIVE
// per-metric comparison (key-mapped, unit-reconciled, tolerance-checked, with a verdict), so this
// renders those with a verdict chip. Before the classifier runs (older studies), it falls back to a
// heuristic exact-key join of the raw targets against the computed metrics. Either way it surfaces the
// metric-key coverage gap (LEARNINGS Phase 2) rather than hiding it.

export interface ComparisonTargetEvidence {
  metric_key: string;
  claimed_value?: number | null;
  unit?: string | null;
  tolerance?: number | null;
  source_locator?: string | null;
}

export interface MetricComparison {
  metric_key: string;
  mapped_key?: string | null;
  // True when the claim mapped only via a qualifier strip (a condition/consensus-qualified peak
  // count). Such a row is basis-sensitive: surfaced with its number + delta, but not scored.
  advisory?: boolean | null;
  claimed_value?: number | null;
  claimed_normalized?: number | null;
  computed_value?: number | null;
  unit?: string | null;
  delta?: number | null;
  within_tolerance?: boolean | null;
  verdict: string; // agree | diverge | not_reported | not_computed
}

export interface ClassificationResult {
  comparisons?: MetricComparison[] | null;
  attribution?: { our_side?: string | null; reasons?: string[] | null } | null;
  coverage?: Record<string, number> | null;
  classification?: string | null;
  auto_finalize?: boolean | null;
  reasoning?: string | null;
}

export interface Evidence {
  computed_metrics?: Record<string, unknown> | null;
  comparison_targets?: ComparisonTargetEvidence[] | null;
  classification_result?: ClassificationResult | null;
  // Level-3 finding-concordance evidence (ADR-069), present once the reproducing step scored E6.
  level3_result?: import("./Level3ResultPanel").Level3Result | null;
  data_run_id?: number | null;
  analysis_run_id?: number | null;
  qc_dashboard_id?: number | null;
}

const VERDICT_META: Record<string, { label: string; cls: string }> = {
  agree: { label: "Agree", cls: "bg-green-100 text-green-800" },
  diverge: { label: "Diverge", cls: "bg-red-100 text-red-800" },
  not_reported: { label: "Not reported", cls: "bg-gray-100 text-gray-600" },
  not_computed: { label: "Not computed", cls: "bg-amber-100 text-amber-800" },
};

// A qualifier-stripped peak count is not scored (agree/diverge would be misleading against a
// basis mismatch), so it gets its own neutral chip that says "evidence, not a verdict".
const ADVISORY_TITLE =
  "Surfaced as evidence, not scored. This peak-count claim is condition/consensus-qualified, so it is " +
  "basis-sensitive (a consensus-across-replicates count vs our per-sample count) and not directly comparable.";

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(4)));
  return String(v);
}

function VerdictChip({ verdict }: { verdict: string }) {
  const meta = VERDICT_META[verdict] ?? { label: verdict, cls: "bg-gray-100 text-gray-600" };
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${meta.cls}`}>{meta.label}</span>
  );
}

export function ValidationEvidenceTable({ evidence }: { evidence: Evidence | null | undefined }) {
  const comparisons = evidence?.classification_result?.comparisons ?? null;
  const computed = evidence?.computed_metrics ?? {};
  const computedEntries = Object.entries(computed);

  // Authoritative path: the classifier's per-metric comparisons.
  if (comparisons && comparisons.length > 0) {
    const mappedKeys = new Set(comparisons.map((c) => c.mapped_key).filter(Boolean) as string[]);
    const computedOnly = computedEntries.filter(([key]) => !mappedKeys.has(key));
    return (
      <div className="space-y-6">
        <div>
          <h3 className="mb-2 text-sm font-semibold text-gray-700">Metric comparison</h3>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-wide text-gray-500">
                  <th scope="col" className="py-2 pr-4">Claimed metric</th>
                  <th scope="col" className="py-2 pr-4">Claimed</th>
                  <th scope="col" className="py-2 pr-4">Computed</th>
                  <th scope="col" className="py-2 pr-4">Δ</th>
                  <th scope="col" className="py-2">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {comparisons.map((c, i) => (
                  <tr key={`${c.metric_key}-${i}`} className="border-b last:border-0">
                    <td className="py-2 pr-4">
                      <span className="font-mono text-xs">{c.metric_key}</span>
                      {c.mapped_key && c.mapped_key !== c.metric_key && (
                        <span
                          className="ml-1 text-xs text-gray-500"
                          title={
                            c.advisory
                              ? "Loosely matched by stripping a condition/consensus qualifier; surfaced as advisory evidence"
                              : "Matched computed QC metric"
                          }
                        >
                          → {c.mapped_key}
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-4">{formatValue(c.claimed_value)}</td>
                    <td className="py-2 pr-4">
                      {c.verdict === "not_computed" ? (
                        <span className="text-gray-500">-</span>
                      ) : (
                        formatValue(c.computed_value)
                      )}
                    </td>
                    <td className="py-2 pr-4 text-gray-500">{c.delta === null || c.delta === undefined ? "" : formatValue(c.delta)}</td>
                    <td className="py-2">
                      {c.advisory ? (
                        <span
                          className="inline-flex items-center rounded bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-800"
                          title={ADVISORY_TITLE}
                        >
                          Advisory
                        </span>
                      ) : (
                        <VerdictChip verdict={c.verdict} />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {computedOnly.length > 0 && <OtherComputed entries={computedOnly} />}
      </div>
    );
  }

  // Fallback (no classifier result yet): heuristic exact-key join of raw targets vs computed.
  const targets = evidence?.comparison_targets ?? [];
  if (targets.length === 0 && computedEntries.length === 0) {
    return (
      <p className="text-sm text-gray-500">
        No evidence has been collected yet. Metrics appear once the analysis run completes.
      </p>
    );
  }

  const targetKeys = new Set(targets.map((t) => t.metric_key));
  const computedOnly = computedEntries.filter(([key]) => !targetKeys.has(key));

  return (
    <div className="space-y-6">
      {targets.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-gray-700">Claimed vs computed</h3>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-wide text-gray-500">
                  <th scope="col" className="py-2 pr-4">Metric</th>
                  <th scope="col" className="py-2 pr-4">Claimed</th>
                  <th scope="col" className="py-2 pr-4">Unit</th>
                  <th scope="col" className="py-2 pr-4">Computed</th>
                  <th scope="col" className="py-2 pr-4">Tolerance</th>
                  <th scope="col" className="py-2">Source</th>
                </tr>
              </thead>
              <tbody>
                {targets.map((t) => {
                  const hasComputed = Object.prototype.hasOwnProperty.call(computed, t.metric_key);
                  return (
                    <tr key={t.metric_key} className="border-b last:border-0">
                      <td className="py-2 pr-4 font-mono text-xs">{t.metric_key}</td>
                      <td className="py-2 pr-4">{formatValue(t.claimed_value)}</td>
                      <td className="py-2 pr-4 text-gray-500">{formatValue(t.unit)}</td>
                      <td className="py-2 pr-4">
                        {hasComputed ? (
                          formatValue(computed[t.metric_key])
                        ) : (
                          <span className="text-gray-500" title="No computed QC metric shares this key">
                            Not reported
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-gray-500">{formatValue(t.tolerance)}</td>
                      <td className="py-2 text-gray-500">{formatValue(t.source_locator)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {computedOnly.length > 0 && <OtherComputed entries={computedOnly} />}
    </div>
  );
}

function OtherComputed({ entries }: { entries: [string, unknown][] }) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-gray-700">Other computed QC metrics</h3>
      <p className="mb-2 text-xs text-gray-500">Computed by the run but not matched to a claim in the paper.</p>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs uppercase tracking-wide text-gray-500">
              <th scope="col" className="py-2 pr-4">Metric</th>
              <th scope="col" className="py-2">Value</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([key, value]) => (
              <tr key={key} className="border-b last:border-0">
                <td className="py-2 pr-4 font-mono text-xs">{key}</td>
                <td className="py-2">{formatValue(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
