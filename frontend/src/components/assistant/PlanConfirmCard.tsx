"use client";

import type { AssistantPlanStep } from "@/lib/types";

// Human titles for the consequential tools a plan can contain. A plan may span several steps
// (e.g. install THEN launch confirmed together), so each step is titled and, when there is more
// than one, numbered.
const STEP_TITLES: Record<string, string> = {
  install: "Install pipeline",
  launch_run: "Launch pipeline run",
  create_experiment: "Create experiment",
  create_sample: "Create sample",
};

// Shared argument labels. A few keys mean different things per tool (e.g. `name` is the pipeline
// for install but the experiment name for create_experiment), so TOOL_LABELS overrides win.
const BASE_LABELS: Record<string, string> = {
  experiment_id: "Experiment",
  pipeline_key: "Pipeline",
  name: "Name",
  version: "Version",
  reference_genome: "Reference genome",
  parameters: "Parameters",
  accessions: "Accessions",
  sample_ids: "Samples",
  external_id: "Sample ID",
  organism: "Organism",
  assay: "Assay",
  molecule_type: "Molecule type",
  library_prep_method: "Library prep",
  chemistry_version: "Chemistry version",
  tissue_type: "Tissue type",
  treatment_condition: "Treatment",
  description: "Description",
  hypothesis: "Hypothesis",
};

const TOOL_LABELS: Record<string, Record<string, string>> = {
  install: { name: "Pipeline" },
};

function labelFor(tool: string, key: string): string {
  return TOOL_LABELS[tool]?.[key] ?? BASE_LABELS[key] ?? key;
}

// Render one proposed step's arguments as readable rows. Consequential steps show their args
// explicitly so the user can catch a wrong entity (sample, pipeline, assay) before confirming.
function StepArgs({ tool, args }: { tool: string; args: Record<string, unknown> }) {
  const entries = Object.entries(args).filter(([, v]) => v !== null && v !== undefined);
  if (entries.length === 0) {
    return <p className="text-sm text-gray-500">No parameters.</p>;
  }
  return (
    <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1 text-sm">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-gray-500">{labelFor(tool, key)}</dt>
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
  // A spend step (e.g. launching a pipeline run) will incur compute cost, so warn before confirm.
  const willSpend = steps.some((s) => s.consequence_class === "spend");
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

      {willSpend && (
        <div
          className="flex items-start gap-2 bg-red-50 border border-red-200 rounded p-2 mb-3"
          data-testid="plan-cost-warning"
        >
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none" className="mt-0.5 shrink-0" aria-hidden="true">
            <path
              d="M10 6.5v4M10 13.5h.01M8.6 3.2L2.3 14a1.6 1.6 0 001.4 2.4h12.6a1.6 1.6 0 001.4-2.4L11.4 3.2a1.6 1.6 0 00-2.8 0z"
              stroke="#dc2626"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <p className="text-xs text-red-700">
            This will spend compute. Confirming starts a real run that may incur cost.
          </p>
        </div>
      )}

      <div className="space-y-3">
        {steps.map((step, i) => (
          <div key={i} className="bg-white rounded border border-amber-200 p-3">
            <p className="text-xs uppercase tracking-wide text-gray-500 mb-2">
              {steps.length > 1 && <span className="text-amber-700">{`Step ${i + 1}: `}</span>}
              {STEP_TITLES[step.tool] ?? step.tool}
            </p>
            <StepArgs tool={step.tool} args={step.args} />
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
