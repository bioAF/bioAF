"use client";

/**
 * plan_6 step 5: the AI decisions behind a reproduction plan, rendered at the C1 gate.
 *
 * Shown in BOTH autonomy modes. The gate is where a person authorises the plan and the spend, and
 * this is the evidence they authorise it on: which model bound which of the paper's claims to a
 * metric bioAF computes, on what reasoning, and how sure it was.
 *
 * A row the alias table resolved is labelled as the alias table's. Presenting a lookup as a model
 * judgment would be the same defect as leaving a model judgment unattributed.
 *
 * change_7.3 section 10 items 5 to 7: each row leads with the claim's science (its sentence, value
 * and unit, population, contrast and cutoff). The internal keys, the model, its confidence and its
 * reason sit under a collapsed "Binding details" element. Mapped is not tested, and the tested count
 * comes from the evidence, never assumed.
 */

export interface AiDecision {
  metric_key: string | null;
  bound_key: string | null;
  resolved: boolean;
  reason: string | null;
  confidence: number | null;
  model: string | null;
  decided_by: string;
  low_confidence: boolean;
  claim_text?: string | null;
  claimed_value?: number | null;
  unit?: string | null;
  population?: string | null;
  contrast?: string | null;
  cutoff?: string | null;
  unresolved_reason?: string | null;
  mapping_status?: string | null;
  mapping_label?: string | null;
  mapping_explanation?: string | null;
}

// change_7.5 section 1.4: a declined QC binding decides the QC check alone. Worded as the projection
// words it, for a row from before the projection carried the words.
const NO_SUPPORTED_METRIC = "No QC metric measures this claim";
const NO_SUPPORTED_EXPLANATION =
  "bioAF computes no QC metric that measures this claim, so it cannot be compared as a QC metric. This does not decide its other checks.";

const MAPPING_CLASS: Record<string, string> = {
  mapped: "bg-emerald-50 text-emerald-700",
  no_supported_metric: "bg-gray-100 text-gray-600",
  failed: "bg-amber-50 text-amber-700",
  unmapped: "bg-gray-100 text-gray-600",
};

/** The mapping in the report's own words. A row from before those words existed is read the same
 * way the projection reads it: resolved is mapped, a model's decline has no supported metric. */
function mappingOf(d: AiDecision): { status: string; label: string; explanation: string | null } {
  if (d.mapping_status && d.mapping_label) {
    return { status: d.mapping_status, label: d.mapping_label, explanation: d.mapping_explanation ?? null };
  }
  if (d.resolved) return { status: "mapped", label: "Mapped to a candidate comparison metric", explanation: null };
  if (d.decided_by === "binding_failed") {
    return {
      status: "failed",
      label: "Mapping could not be made",
      explanation: "The model's answer could not be read, so this claim is not mapped to a metric.",
    };
  }
  if (d.decided_by === "model") {
    return { status: "no_supported_metric", label: NO_SUPPORTED_METRIC, explanation: NO_SUPPORTED_EXPLANATION };
  }
  return { status: "unmapped", label: "Not mapped", explanation: "No mapping decision was recorded for this claim." };
}

function humanize(key: string | null): string {
  return (key ?? "claim").replace(/_/g, " ");
}

function countsLabel(mapped: number, tested: number): string {
  const noun = mapped === 1 ? "claim" : "claims";
  return `${mapped} ${noun} mapped to candidate comparison metrics; ${tested === 0 ? "none tested" : `${tested} tested`}.`;
}

export function AiDecisionList({ decisions, testedCount = 0 }: { decisions: AiDecision[]; testedCount?: number }) {
  if (!decisions || decisions.length === 0) return null;

  const mapped = decisions.filter((d) => d.resolved).length;

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">AI decisions</p>
        {/* The model that decided is attributed on every row, under its binding details. */}
        <p className="text-xs text-gray-500">{countsLabel(mapped, testedCount)}</p>
      </div>
      <ul className="mt-1 divide-y divide-gray-100 border-y border-gray-100">
        {decisions.map((d, i) => {
          const mapping = mappingOf(d);
          const context = [d.population, d.contrast, d.cutoff].filter(Boolean);
          return (
            <li key={i} className="py-1.5 text-sm">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-gray-800">{d.claim_text || humanize(d.metric_key)}</span>
                {d.claimed_value !== null && d.claimed_value !== undefined && (
                  <span className="tabular-nums text-xs text-gray-700">
                    {d.claimed_value}
                    {d.unit ? ` ${d.unit}` : ""}
                  </span>
                )}
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                    MAPPING_CLASS[mapping.status] ?? "bg-gray-100 text-gray-600"
                  }`}
                >
                  {mapping.label}
                </span>
                {d.low_confidence && (
                  <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-700">
                    Low confidence
                  </span>
                )}
              </div>
              {context.length > 0 && <p className="text-xs text-gray-500">{context.join(" · ")}</p>}
              {mapping.explanation && <p className="text-xs text-gray-500">{mapping.explanation}</p>}
              {d.unresolved_reason && <p className="text-xs text-amber-700">Unresolved: {d.unresolved_reason}</p>}
              <details className="mt-0.5 text-xs text-gray-500">
                <summary className="cursor-pointer select-none">Binding details</summary>
                <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2">
                  <span className="font-mono text-gray-700">{d.metric_key}</span>
                  <span aria-hidden>&rarr;</span>
                  <span className="font-mono text-gray-700">{d.bound_key ?? "none"}</span>
                  {d.decided_by === "model" ? (
                    <>
                      {d.confidence !== null && <span className="tabular-nums">{d.confidence.toFixed(2)}</span>}
                      {d.model && <span className="font-mono">{d.model}</span>}
                    </>
                  ) : (
                    <span>{d.decided_by === "binding_failed" ? "binding failed" : "alias table"}</span>
                  )}
                </div>
                {d.reason && <p>{d.reason}</p>}
              </details>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
