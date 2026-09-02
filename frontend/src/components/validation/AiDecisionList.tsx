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
}

export function AiDecisionList({ decisions }: { decisions: AiDecision[] }) {
  if (!decisions || decisions.length === 0) return null;

  const resolved = decisions.filter((d) => d.resolved).length;
  const models = Array.from(
    new Set(decisions.map((d) => d.model).filter((m): m is string => !!m)),
  );

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          AI decisions
        </p>
        <p className="text-xs text-gray-500">
          {`Resolved ${resolved} of ${decisions.length} claims`}
          {models.length > 0 ? ` · model: ${models.join(", ")}` : ""}
        </p>
      </div>
      <ul className="mt-1 divide-y divide-gray-100 border-y border-gray-100">
        {decisions.map((d, i) => (
          <li key={i} className="flex flex-wrap items-baseline gap-x-2 py-1.5 text-sm">
            <span className="font-mono text-xs text-gray-700">{d.metric_key}</span>
            <span aria-hidden className="text-gray-500">
              &rarr;
            </span>
            {d.resolved ? (
              <span className="font-mono text-xs font-semibold text-gray-900">
                {d.bound_key}
              </span>
            ) : (
              <span className="text-xs font-semibold text-gray-500">Declined</span>
            )}
            {d.decided_by === "model" ? (
              d.confidence !== null && (
                <span className="text-xs tabular-nums text-gray-600">
                  {d.confidence.toFixed(2)}
                </span>
              )
            ) : (
              <span className="text-xs text-gray-500">alias table</span>
            )}
            {d.low_confidence && (
              <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-700">
                Low confidence
              </span>
            )}
            {d.reason && (
              <span className="basis-full text-xs text-gray-500">{d.reason}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
