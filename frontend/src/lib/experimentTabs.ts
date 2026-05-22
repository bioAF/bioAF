/** Tab keys for the experiment detail page, shared with the deep-link resolver. */
export const EXPERIMENT_TAB_KEYS = [
  "overview",
  "samples",
  "batches",
  "files",
  "literature",
  "analysis",
  "pipelines",
  "results",
  "provenance",
  "audit",
  "agent_review",
] as const;

export type ExperimentTabKey = (typeof EXPERIMENT_TAB_KEYS)[number];

/**
 * Pick the experiment detail tab to open from a `?tab=` query param. Used so a
 * notification (e.g. a file associated with an experiment) can land directly on
 * the right tab. Unknown or missing values fall back to the overview tab.
 */
export function resolveExperimentTab(param: string | null | undefined): ExperimentTabKey {
  if (param && (EXPERIMENT_TAB_KEYS as readonly string[]).includes(param)) {
    return param as ExperimentTabKey;
  }
  return "overview";
}
