// The lifecycle stages a validation study moves through on the way to a classification. This is the
// "in progress" display contract: while a study is NOT yet classified, the UI shows WHERE it is in the
// pipeline rather than a validation badge. A null confidence on a still-running study means "not done
// yet", which is deliberately different from "Could Not Reproduce" (see lib/validationStatus and
// local/lit_validation/LEARNINGS.md). Mirrors the backend state machine
// (app/models/validation_study.py VALIDATION_STUDY_*).

export type ValidationStageKind =
  | "in_progress"      // an automated step is running
  | "awaiting_review"  // a human gate (approve the plan / classify by hand)
  | "declined"         // the plan was declined at the C1 gate (terminal)
  | "error"            // an infrastructure failure (terminal, retryable)
  | "classified";      // reached a verdict (the page renders a badge, not a stage)

export interface ValidationStage {
  state: string;
  label: string;
  description: string;
  kind: ValidationStageKind;
  // 1-based position along the happy path, or null for off-path terminals
  // (classified, plan_declined, error) and unknown states.
  step: number | null;
  totalSteps: number;
}

// The happy path the user watches, in order. `classified` is the outcome (rendered as a validation
// badge), so it is not a step here. `plan_ready` and `comparing` are the two human gates.
export const VALIDATION_HAPPY_PATH: ReadonlyArray<{
  state: string;
  label: string;
  description: string;
  kind: ValidationStageKind;
}> = [
  { state: "requested", label: "Requested", description: "Validation requested; queued to read the paper.", kind: "in_progress" },
  { state: "acquiring_text", label: "Fetching paper", description: "Retrieving the paper's full text.", kind: "in_progress" },
  { state: "reading", label: "Reading paper", description: "Extracting the reproduction plan from the paper.", kind: "in_progress" },
  { state: "plan_ready", label: "Plan ready", description: "The reproduction plan is ready for a scientist to approve.", kind: "awaiting_review" },
  { state: "acquiring_data", label: "Fetching data", description: "Downloading the deposited sequencing data.", kind: "in_progress" },
  { state: "setup", label: "Setting up", description: "Preparing the analysis run.", kind: "in_progress" },
  { state: "running", label: "Running analysis", description: "Running the reproduction pipeline.", kind: "in_progress" },
  { state: "extracting", label: "Extracting metrics", description: "Reading QC metrics from the run outputs.", kind: "in_progress" },
  { state: "comparing", label: "Awaiting review", description: "Computed metrics are ready to compare against the paper's claims.", kind: "awaiting_review" },
];

const TOTAL_STEPS = VALIDATION_HAPPY_PATH.length;

const OFF_PATH: Record<string, { label: string; description: string; kind: ValidationStageKind }> = {
  classified: {
    label: "Classified",
    description: "The study reached a verdict.",
    kind: "classified",
  },
  plan_declined: {
    label: "Plan declined",
    description: "A scientist declined the reproduction plan; no compute was spent.",
    kind: "declined",
  },
  error: {
    label: "Error",
    description: "The validation run hit an infrastructure error and can be retried.",
    kind: "error",
  },
};

/**
 * Describe a study's current `state` for the in-progress display. Total for any string: an off-path
 * terminal (classified / plan_declined / error) or an unknown state returns a descriptor with a null
 * step. The page renders a validation badge for a classified study and this stage for everything else.
 */
export function getValidationStage(state: string): ValidationStage {
  const idx = VALIDATION_HAPPY_PATH.findIndex((s) => s.state === state);
  if (idx >= 0) {
    const step = VALIDATION_HAPPY_PATH[idx];
    return {
      state,
      label: step.label,
      description: step.description,
      kind: step.kind,
      step: idx + 1,
      totalSteps: TOTAL_STEPS,
    };
  }

  const off = OFF_PATH[state];
  if (off) {
    return { state, label: off.label, description: off.description, kind: off.kind, step: null, totalSteps: TOTAL_STEPS };
  }

  // Unknown/future state: stay total and inert (the page never relies on this branch).
  return { state, label: state || "Unknown", description: "", kind: "in_progress", step: null, totalSteps: TOTAL_STEPS };
}
