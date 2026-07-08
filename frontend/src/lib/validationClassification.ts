// The six terminal classification buckets a human records at the `comparing` gate (spec-03). The
// classifier states facts; there is no "bad" label. Kept in sync with the backend
// VALIDATION_STUDY_CLASSIFICATIONS (app/models/validation_study.py).

export interface ValidationClassificationOption {
  value: string;
  label: string;
  description: string;
}

export const VALIDATION_CLASSIFICATIONS: ReadonlyArray<ValidationClassificationOption> = [
  { value: "validated", label: "Validated", description: "Computed metrics agree with the paper's claims." },
  { value: "not_validated", label: "Not validated", description: "Computed metrics contradict the paper's claims." },
  { value: "missing_data", label: "Missing data", description: "No usable deposited data to reproduce from." },
  { value: "missing_methods", label: "Missing methods", description: "The methods are too thin to reproduce." },
  { value: "not_reproducible", label: "Not reproducible", description: "No equivalent pipeline could run the analysis." },
  { value: "inconclusive", label: "Inconclusive", description: "Ran, but divergence could not be attributed." },
];
