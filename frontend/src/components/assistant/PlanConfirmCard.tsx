"use client";

import type { AssistantPlanStep } from "@/lib/types";

// Human titles for the consequential tools a plan can contain. A plan may span several steps
// (e.g. install THEN launch confirmed together), so each step is titled and, when there is more
// than one, numbered.
const STEP_TITLES: Record<string, string> = {
  install: "Install pipeline",
  launch_run: "Launch pipeline run",
};

// Render one proposed step's arguments as readable rows. launch_run is the v1 spend action;
// its args are shown explicitly so the user can catch a wrong entity before confirming.
function StepArgs({ args }: { args: Record<string, unknown> }) {
  const labels: Record<string, string> = {
    experiment_id: "Experiment",
    pipeline_key: "Pipeline",
    name: "Pipeline",
    version: "Version",
    reference_genome: "Reference genome",
    parameters: "Parameters",
    accessions: "Accessions",
  };
  const entries = Object.entries(args).filter(([, v]) => v !== null && v !== undefined);
  if (entries.length === 0) {
    return <p className="text-sm text-gray-500">No parameters.</p>;
  }
  return (
    <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-gray-500">{labels[key] ?? key}</dt>
          <dd className="text-gray-900 font-medium break-words">
            {typeof value === "object" ? (
              <code className="text-xs">{JSON.stringify(value)}</code>
            ) : (
              String(value)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function PlanConfirmCard({
  steps,
  busy,
  resolved,
  onConfirm,
  onCancel,
}: {
  steps: AssistantPlanStep[];
  busy: boolean;
  resolved?: "approved" | "cancelled";
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      className="bg-amber-50 border border-amber-200 rounded-lg p-4"
      data-testid="plan-confirm-card"
    >
      <p className="text-sm font-semibold text-amber-900 mb-2">
        Confirm before this runs
      </p>
      <p className="text-xs text-amber-800 mb-3">
        The assistant has prepared the {steps.length > 1 ? `${steps.length} steps` : "action"} below.
        Review {steps.length > 1 ? "them" : "it"} and confirm to proceed. Nothing runs until you
        confirm.
      </p>

      <div className="space-y-3">
        {steps.map((step, i) => (
          <div key={i} className="bg-white rounded border border-amber-200 p-3">
            <p className="text-xs uppercase tracking-wide text-gray-500 mb-2">
              {steps.length > 1 && <span className="text-amber-700">{`Step ${i + 1}: `}</span>}
              {STEP_TITLES[step.tool] ?? step.tool}
            </p>
            <StepArgs args={step.args} />
          </div>
        ))}
      </div>

      {resolved === "approved" && (
        <p className="mt-3 text-sm font-medium text-green-700" data-testid="plan-approved">
          Approved.
        </p>
      )}
      {resolved === "cancelled" && (
        <p className="mt-3 text-sm font-medium text-gray-500" data-testid="plan-cancelled">
          Cancelled.
        </p>
      )}

      {!resolved && (
        <div className="mt-4 flex gap-2">
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="bg-bioaf-600 text-white px-4 py-2 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
          >
            {busy ? "Confirming..." : "Confirm"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="border border-gray-300 text-gray-700 px-4 py-2 rounded text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
