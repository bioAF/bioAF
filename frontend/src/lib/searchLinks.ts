/** A single hit from the header quick-search (`GET /api/search/quick`). */
export interface QuickSearchHit {
  entity_type: string;
  entity_id: number;
  name: string;
  experiment_id?: number | null;
}

/** The in-app route to jump to for a quick-search hit. */
export function searchHitHref(hit: QuickSearchHit): string {
  switch (hit.entity_type) {
    case "experiment":
      return `/experiments/${hit.entity_id}`;
    case "sample":
      return hit.experiment_id != null
        ? `/experiments/${hit.experiment_id}?tab=samples`
        : "/experiments";
    case "pipeline_run":
      return `/pipelines/runs/${hit.entity_id}`;
    case "file":
      return `/data/files?file=${hit.entity_id}`;
    case "lab_document":
      return `/lab-knowledge/documents/${hit.entity_id}`;
    case "lab_glossary_term":
      return `/lab-knowledge/glossary?term=${hit.entity_id}`;
    case "sdr":
      return `/lab-knowledge/decision-records/${hit.entity_id}`;
    default:
      return "/dashboard";
  }
}

/** Human label for a hit's entity type, shown as a small badge in results. */
export function searchHitTypeLabel(entityType: string): string {
  switch (entityType) {
    case "experiment":
      return "Experiment";
    case "sample":
      return "Sample";
    case "pipeline_run":
      return "Run";
    case "file":
      return "File";
    case "project":
      return "Project";
    case "pipeline_definition":
      return "Pipeline";
    case "literature_paper":
      return "Paper";
    case "lab_document":
      return "Lab Document";
    case "lab_glossary_term":
      return "Glossary";
    case "sdr":
      return "SDR";
    default:
      return entityType;
  }
}
