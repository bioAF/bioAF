import { ValidationStatusBadge } from "@/components/validation/ValidationStatusBadge";
import { getValidationStage } from "@/lib/validationStage";
import { statusBadgeClass } from "@/lib/statusStyles";

function humanizeClassification(c: string): string {
  return c.replace(/_/g, " ").replace(/^\w/, (m) => m.toUpperCase());
}

// plan_7 step 9. A verdict reached from the authors' own deposited matrix tests their statistics,
// not their processing: it cannot detect a processing error, a swapped sample or a contaminated
// library. It is a strictly weaker claim than a raw-reads verdict and must not render as the same
// thing. BOTH routes are labelled, so neither reads as the unmarked default.
//
// Domain wording, not simplified: "Deposited data" and "Raw reads" are what a bioinformatician
// calls these.
const ROUTE_LABEL: Record<string, { label: string; title: string }> = {
  deposit: {
    label: "Deposited data",
    title:
      "Reproduced from the data the authors deposited. The upstream processing was not independently repeated.",
  },
  pipeline: {
    label: "Raw reads",
    title: "Reproduced by re-running the paper's analysis from its raw sequencing reads.",
  },
};

/**
 * The top-line outcome of a validation study. Gated on `state`: a classified study renders the
 * confidence-derived validation badge; any other state renders its pipeline stage instead. This gate
 * is deliberate: a null confidence on a still-running study means "not done yet", not "Could Not
 * Reproduce" (see lib/validationStage and local/lit_validation/LEARNINGS.md).
 */
export function ValidationStudyOutcome({
  state,
  confidence,
  classification,
  failureReason,
  route,
}: {
  state: string;
  confidence: number | null | undefined;
  classification?: string | null;
  failureReason?: string | null;
  route?: string | null;
}) {
  if (state === "classified") {
    return (
      <span className="inline-flex items-center gap-2">
        <ValidationStatusBadge confidence={confidence} classification={classification} />
        {classification && classification !== "partially_reproduced" && (
          <span className="text-xs text-gray-500" title="Classification bucket">
            {humanizeClassification(classification)}
          </span>
        )}
        {route && ROUTE_LABEL[route] && (
          <span
            className="inline-flex items-center rounded border border-gray-300 px-1.5 py-0.5 text-xs text-gray-600"
            title={ROUTE_LABEL[route].title}
          >
            {ROUTE_LABEL[route].label}
          </span>
        )}
      </span>
    );
  }

  const stage = getValidationStage(state);
  return (
    <span className="inline-flex items-center gap-2">
      <span
        title={stage.description}
        className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${statusBadgeClass("validationStage", stage.kind)}`}
      >
        {stage.label}
      </span>
      {stage.step !== null && (
        <span className="text-xs text-gray-500">
          Step {stage.step} of {stage.totalSteps}
        </span>
      )}
      {state === "error" && failureReason && (
        <span className="text-xs text-red-700">{failureReason}</span>
      )}
    </span>
  );
}
