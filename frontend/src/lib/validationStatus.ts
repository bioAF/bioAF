// Map a validation-confidence percentage (0-100: "how confident that the paper's results were
// validated") to the status the UI reports.
//
// A null/undefined/NaN confidence means validation could not be run at all -> "Could Not Reproduce".
// This is deliberately distinct from a LOW confidence: "we could not test it" is not the same as "we
// tested it and it is unlikely" (see local/lit_validation/LEARNINGS.md). The backend supplies the
// confidence (Phase 2 E2); this module is the display contract.
//
// Bands are contiguous and half-open, checked highest-first, so the ranges the spec listed with an
// overlap (75) and gaps (54-55, 99-100) resolve deterministically: 75 -> Likely, 54.x -> Questionable,
// 99.x -> Likely, exactly 100 -> Fully.

export type ValidationStatusKey =
  | "fully_validated"
  | "likely_validated"
  | "possibly_validated"
  | "questionable"
  | "unlikely"
  | "very_unlikely"
  | "could_not_reproduce";

export type ValidationStatusTone = "positive" | "caution" | "negative" | "neutral";

export interface ValidationStatus {
  key: ValidationStatusKey;
  label: string;
  tone: ValidationStatusTone;
  needsHumanReview: boolean;
  description: string;
}

const COULD_NOT_REPRODUCE: ValidationStatus = {
  key: "could_not_reproduce",
  label: "Could Not Reproduce",
  tone: "neutral",
  needsHumanReview: false,
  description: "Validation could not be run for this study.",
};

// Ordered highest-first; the first band whose lower bound the confidence meets wins.
const BANDS: ReadonlyArray<{ min: number; status: ValidationStatus }> = [
  {
    min: 100,
    status: {
      key: "fully_validated",
      label: "Fully Validated",
      tone: "positive",
      needsHumanReview: false,
      description: "100% confident the results were validated.",
    },
  },
  {
    min: 75,
    status: {
      key: "likely_validated",
      label: "Likely Validated",
      tone: "positive",
      needsHumanReview: true,
      description: "75-99% confident the results were validated. Needs human review.",
    },
  },
  {
    min: 55,
    status: {
      key: "possibly_validated",
      label: "Possibly Validated",
      tone: "caution",
      needsHumanReview: true,
      description: "55-74% confident the results were validated. Needs human review.",
    },
  },
  {
    min: 25,
    status: {
      key: "questionable",
      label: "Questionable",
      tone: "caution",
      needsHumanReview: true,
      description: "25-54% confident the results were validated. Needs human review.",
    },
  },
  {
    min: 5,
    status: {
      key: "unlikely",
      label: "Unlikely",
      tone: "negative",
      needsHumanReview: false,
      description: "5-24% confident the results were validated.",
    },
  },
  {
    min: 0,
    status: {
      key: "very_unlikely",
      label: "Very Unlikely",
      tone: "negative",
      needsHumanReview: false,
      description: "0-4% confident the results were validated.",
    },
  },
];

export function getValidationStatus(confidencePct: number | null | undefined): ValidationStatus {
  if (confidencePct === null || confidencePct === undefined || Number.isNaN(confidencePct)) {
    return COULD_NOT_REPRODUCE;
  }
  const c = Math.max(0, Math.min(100, confidencePct));
  for (const band of BANDS) {
    if (c >= band.min) return band.status;
  }
  // c is clamped to >= 0 and the last band has min 0, so this is unreachable; keep it total.
  return COULD_NOT_REPRODUCE;
}
