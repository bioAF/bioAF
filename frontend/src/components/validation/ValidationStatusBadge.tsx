import { getValidationStatus, type ValidationStatusTone } from "@/lib/validationStatus";

// Tone -> palette, matching the app's status colors (see lib/statusStyles).
const TONE_CLASSES: Record<ValidationStatusTone, string> = {
  positive: "bg-green-100 text-green-800",
  caution: "bg-yellow-100 text-yellow-800",
  negative: "bg-red-100 text-red-800",
  neutral: "bg-gray-100 text-gray-700",
};

/**
 * Renders a validation study's outcome as a pill, from a confidence percentage
 * (0-100, "how confident the results were validated"). A null/undefined
 * confidence renders "Could Not Reproduce". The middle bands surface a
 * "Needs review" hint (suppress with showReview={false}); the band description
 * is exposed as a tooltip. `classification` lets a bucket that does not map onto
 * the confidence scale (e.g. partially_reproduced) render its precise label.
 */
export function ValidationStatusBadge({
  confidence,
  classification,
  showReview = true,
}: {
  confidence: number | null | undefined;
  classification?: string | null;
  showReview?: boolean;
}) {
  const status = getValidationStatus(confidence, classification);
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        title={status.description}
        className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[status.tone]}`}
      >
        {status.label}
      </span>
      {showReview && status.needsHumanReview && (
        <span className="text-xs font-medium text-yellow-700" title="A human should review this result">
          Needs review
        </span>
      )}
    </span>
  );
}
