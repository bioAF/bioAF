// The computed-vs-claimed evidence a scientist reads to classify a study by hand at the `comparing`
// gate (Phase 1 keeps comparison manual). The extractor's ComparisonTarget keys and the QC dashboard's
// computed metric keys are DIFFERENT vocabularies and do not auto-join (local/lit_validation/LEARNINGS.md
// "the important one for Phase 2"). So this table joins on exact key where it can, marks an unmatched
// target "Not reported", and lists the computed metrics that have no target separately, rather than
// hiding the gap. Phase 2 (E2) replaces the manual read with an automatic verdict.

export interface ComparisonTargetEvidence {
  metric_key: string;
  claimed_value?: number | null;
  unit?: string | null;
  tolerance?: number | null;
  source_locator?: string | null;
}

export interface Evidence {
  computed_metrics?: Record<string, unknown> | null;
  comparison_targets?: ComparisonTargetEvidence[] | null;
  data_run_id?: number | null;
  analysis_run_id?: number | null;
  qc_dashboard_id?: number | null;
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  return String(v);
}

export function ValidationEvidenceTable({ evidence }: { evidence: Evidence | null | undefined }) {
  const targets = evidence?.comparison_targets ?? [];
  const computed = evidence?.computed_metrics ?? {};
  const computedEntries = Object.entries(computed);

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
                  <th className="py-2 pr-4">Metric</th>
                  <th className="py-2 pr-4">Claimed</th>
                  <th className="py-2 pr-4">Unit</th>
                  <th className="py-2 pr-4">Computed</th>
                  <th className="py-2 pr-4">Tolerance</th>
                  <th className="py-2">Source</th>
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
                          <span className="text-gray-400" title="No computed QC metric shares this key">
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

      {computedOnly.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-gray-700">Other computed QC metrics</h3>
          <p className="mb-2 text-xs text-gray-500">
            Computed by the run but not claimed in the paper. Metric keys differ between the two, so a
            human maps them by hand in Phase 1.
          </p>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-wide text-gray-500">
                  <th className="py-2 pr-4">Metric</th>
                  <th className="py-2">Value</th>
                </tr>
              </thead>
              <tbody>
                {computedOnly.map(([key, value]) => (
                  <tr key={key} className="border-b last:border-0">
                    <td className="py-2 pr-4 font-mono text-xs">{key}</td>
                    <td className="py-2">{formatValue(value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
