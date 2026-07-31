import { ValidationStatusBadge } from "@/components/validation/ValidationStatusBadge";
import { getValidationStage } from "@/lib/validationStage";
import { statusBadgeClass } from "@/lib/statusStyles";

function humanizeClassification(c: string): string {
  return c.replace(/_/g, " ").replace(/^\w/, (m) => m.toUpperCase());
}

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
}: {
  state: string;
  confidence: number | null | undefined;
  classification?: string | null;
  failureReason?: string | null;
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
