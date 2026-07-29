// The terminal classification buckets a human records at the `comparing` gate (spec-03). The
// classifier states facts; there is no "bad" label. Kept in sync with the backend
// VALIDATION_STUDY_CLASSIFICATIONS (app/models/validation_study.py).

export interface ValidationClassificationOption {
  value: string;
  label: string;
  description: string;
}

export const VALIDATION_CLASSIFICATIONS: ReadonlyArray<ValidationClassificationOption> = [
  { value: "validated", label: "Validated", description: "Computed metrics agree with the paper's claims." },
  {
    value: "partially_reproduced",
    label: "Partially reproduced",
    description: "The paper's finding reproduced in part: the overlap is statistically real, but recovery was incomplete.",
  },
  { value: "not_validated", label: "Not validated", description: "Computed metrics contradict the paper's claims." },
  { value: "missing_data", label: "Missing data", description: "No usable deposited data to reproduce from." },
  { value: "missing_methods", label: "Missing methods", description: "The methods are too thin to reproduce." },
  { value: "not_reproducible", label: "Not reproducible", description: "No equivalent pipeline could run the analysis." },
  { value: "inconclusive", label: "Inconclusive", description: "Ran, but divergence could not be attributed." },
];

export type ValidationTone = "positive" | "negative" | "caution" | "neutral";

const CLASSIFICATION_TONE: Record<string, ValidationTone> = {
  validated: "positive",
  partially_reproduced: "caution",
  not_validated: "negative",
  missing_data: "neutral",
  missing_methods: "neutral",
  not_reproducible: "neutral",
  inconclusive: "caution",
};

export function classificationLabel(value: string | null | undefined): string {
  if (!value) return "";
  const found = VALIDATION_CLASSIFICATIONS.find((c) => c.value === value);
  return found ? found.label : value.replace(/_/g, " ").replace(/^\w/, (m) => m.toUpperCase());
}

export function classificationTone(value: string | null | undefined): ValidationTone {
  return (value && CLASSIFICATION_TONE[value]) || "neutral";
}
