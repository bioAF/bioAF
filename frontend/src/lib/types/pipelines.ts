// Pipeline catalog, custom pipelines, runs and run reviews.
// Re-exported by the compatibility barrel `@/lib/types`; import siblings directly, never the barrel.

import type { paths } from "../../../contracts/internal-api";
import type { ExperimentReference } from "./experiments";
import type { UserSummary } from "./identity";

export type PipelineRunStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

export type PipelineProcessStatus = "pending" | "running" | "completed" | "failed" | "cached";

export interface ParameterGroup {
  title?: string;
  properties?: Record<string, {
    type?: string;
    description?: string;
    default?: unknown;
    enum?: string[];
    hidden?: boolean;
    help_text?: string;
    format?: string;
    minimum?: number;
    maximum?: number;
    fa_icon?: string;
  }>;
  required?: string[];
}

export interface ParameterSchema {
  /** JSON Schema 2020-12 renamed `definitions` to `$defs`, and the current
   *  nf-core template emits `$defs`. Both spellings are in the catalog today
   *  (13 `$defs` vs 7 `definitions` across the 20 most popular pipelines), so
   *  any reader must accept either. */
  $defs?: Record<string, ParameterGroup>;
  definitions?: Record<string, ParameterGroup>;
}

export interface PipelineCatalog {
  id: number;
  pipeline_key: string;
  name: string;
  description: string | null;
  source_type: string;
  source_url: string | null;
  version: string | null;
  parameter_schema: ParameterSchema | null;
  /** Required samplesheet columns the user must answer at launch, from the
   *  pipeline's assets/schema_input.json. Distinct from parameter_schema, which
   *  carries nextflow_schema.json's pipeline parameters. A samplesheet column
   *  such as instrument_platform appears in neither the params nor any other
   *  form, which is why this exists. */
  samplesheet_inputs: SamplesheetInputSpec[];
  default_params: Record<string, unknown> | null;
  is_builtin: boolean;
  enabled: boolean;
  custom_pipeline_id: number | null;
  created_by_username: string | null;
  latest_version_number: number | null;
}

/** One samplesheet column collected at launch. Only columns whose value is
 *  constant across the run appear, so a single answer fills every row.
 *  `allowed_values` comes from the pipeline's own schema; empty means free text. */
export interface SamplesheetInputSpec {
  name: string;
  parameter: string;
  required: boolean;
  allowed_values: string[];
}

/** One column the entry grid must collect PER SAMPLE, from the preflight.
 *  Derived from the same computation as the launch block, so the questions
 *  asked and the refusal given cannot disagree. */
export interface PerSampleInputSpec {
  name: string;
  required: boolean;
  is_file: boolean;
  /** The Sample field this column corresponds to, when it is one. Set means the
   *  value is a fact about the sample rather than a pipeline parameter, which is
   *  what decides whether the pipeline's vocabulary may constrain it. */
  sample_field: string | null;
  allowed_values: string[];
  /** True only for a pipeline PARAMETER with an enum. A field recorded on the
   *  sample is never fenced by a pipeline's list: XX/XY/NA is sarek's input
   *  constraint, not a vocabulary for sex. */
  constrained: boolean;
  description: string | null;
  format_hint: string | null;
  required_by: string | null;
  reason: string | null;
  samples: { id: number; external_id: string | null }[];
}

/** A saved design offered to this launch, and what it does not cover.
 *  Never applied on bioAF's own initiative: a grouping that was right for six
 *  samples may be wrong for twelve, and a prefilled value looks plausible
 *  precisely because it was correct last time. */
export interface SamplesheetPrefill {
  /** Which rung it came from, or null when nothing applies. Naming it is what
   *  keeps an inherited organisation-wide binding from looking identical to one
   *  somebody set for this experiment. */
  scope: "experiment" | "project" | "organization" | null;
  values: Record<string, Record<string, string>>;
  bindings: Record<string, string>;
  /** The columns declared for a pipeline that publishes no contract of its own.
   *  Absent or empty means nothing was declared, which keeps today's standard
   *  sheet. Optional because a client must not require a field a backend
   *  deployed before this existed does not send. */
  columns?: DeclaredColumn[];
  samples_without_values: number[];
}

/** Where a declared column's value comes from. A column with no binding at all
 *  is asked per sample in the entry grid: a co-assembly grouping or a
 *  differential contrast is a design decision no binding can derive. */
export type BindingSource = "read" | "sample_field" | "file_type" | "custom_field" | "literal";

/** One column of a samplesheet a SCIENTIST declared, for a pipeline that
 *  publishes none. Same {name, type, required} shape the experiment field
 *  editor uses, plus the binding an intake field does not have. */
export interface DeclaredColumn {
  name: string;
  type: string;
  required: boolean;
  binding?: { source: BindingSource; key: string };
}

/** The sheet a launch would submit, and what it leaves out. */
export interface SamplesheetPreview {
  columns: string[];
  /** Each row names the sample it belongs to, so a wrongly-resolved cell is
   *  corrected against a sample rather than against a position. */
  rows: { sample_id: number | null; external_id: string | null; values: string[] }[];
  csv: string;
  /** Values the pipeline's own vocabulary could not express, dropped from the
   *  sheet. Invisible otherwise: the column is simply absent, which reads as
   *  "this pipeline does not ask for it". */
  omissions: {
    column: string;
    sample_id: number | null;
    external_id: string | null;
    value: string;
    reason: string;
    allowed_values: string[];
  }[];
}

/** Whether a launch would succeed, asked before anything is created. Mirrors
 *  the domain error the launch itself would raise, so the dialog shows the same
 *  explanation while the user can still act on it. */
export interface PipelineRunPreflight {
  can_launch: boolean;
  code: string | null;
  reason: string | null;
  details: {
    required_inputs?: string[];
    // The samples carrying no input file at all, for the `samples_missing_files`
    // verdict. A different kind of gap from `missing_columns`: no value typed
    // into a grid resolves it, so the block offers to drop them instead.
    samples_without_files?: { id: number; external_id: string | null }[];
    missing_columns?: Record<
      string,
      {
        sample_field: string | null;
        allowed_values: string[];
        samples: {
          id: number;
          external_id: string | null;
          // The value the pipeline objected to, and a spelling it would accept.
          // bioAF recommends rather than substitutes: the scientist decides what
          // the field says, so the suggestion is offered, never applied.
          value?: string | null;
          suggestion?: string | null;
        }[];
        // Why this column is being reported: "missing", "required_by",
        // "not_accepted", "invalid_characters", "collision", "not_unique",
        // "no_matching_file" or "empty_in_row". Each needs different words,
        // because "missing" sends someone who just typed a value to look for the
        // wrong problem, and "empty_in_row" sends them to look for a field when
        // what is absent is a FILE: the sample has reads, and one of the rows
        // about to be emitted for it does not.
        reason?: string | null;
        // The schema's own regex, so a scientist who would rather choose their
        // own spelling than take the suggestion can see the rule.
        pattern?: string | null;
        // Set when the pipeline requires this column only because another one is
        // filled (mag: short reads oblige a platform). Without it the notice
        // reports a column the schema's own required list does not mention.
        required_by?: string | null;
        // The other columns this one must be unique WITHIN, when the schema
        // declares a uniqueness rule. mag pairs `run` with `sample`.
        unique_with?: string[] | null;
        // What would actually let the run through, where typing a value would
        // not. Absent for the ordinary case, where bioAF has no value and the
        // scientist may well have one, so the entry grid asks.
        //
        // "merge_reads": the rows came off ONE sequencing run and differ only by
        // lane, so any value separating them is a lane wearing a run's name.
        // "one_row_per_sample": the rule is on the sample's own name alone
        // (ampliseq), so the only field on offer is the one they must not
        // change. A column carrying either is left OUT of the entry grid, and
        // this is what says why.
        remedy?: "merge_reads" | "one_row_per_sample" | null;
        // The sequencing runs that contribute more than one row, for a block
        // that has to name them. Only populated for "merge_reads". `source`
        // separates "flow cell HLK3VDSX7" from "run SRR111", which are different
        // claims and only one is true of any given row.
        repeated?: { run: string; source: "flowcell" | "accession"; lanes: string[] }[] | null;
      }
    >;
  };
  /** The sheet this run would submit, rendered as a table by the review step. */
  samplesheet?: SamplesheetPreview;
  /** The columns an entry grid must collect. */
  per_sample_inputs?: PerSampleInputSpec[];
  /** What a saved design would contribute, offered rather than applied. */
  prefill?: SamplesheetPrefill;
  /** Whether this pipeline's sheet is one a scientist declares, and the
   *  vocabulary to bind its columns against. */
  declaration?: {
    /** True only when the pipeline publishes no contract AND no tailored
     *  generator owns it. Declaring columns for either would collect an answer
     *  bioAF then ignores. */
    declarable: boolean;
    /** The file types these samples actually carry. */
    file_types: string[];
    custom_fields: string[];
  };
}

export interface PipelineCatalogListResponse {
  pipelines: PipelineCatalog[];
  total: number;
}

export interface RegistryPipeline {
  name: string;
  full_name: string;
  description: string | null;
  topics: string[];
  stars: number | null;
  latest_release: string | null;
  archived: boolean;
  installed: boolean;
  installed_version: string | null;
  update_available: boolean;
}

export interface RegistryListResponse {
  pipelines: RegistryPipeline[];
  total: number;
  last_refreshed_at: string | null;
}

export interface RegistryVersion {
  tag_name: string;
  published_at: string | null;
  has_schema: boolean;
}

export interface RegistryVersionsResponse {
  name: string;
  versions: RegistryVersion[];
}

export interface RegistryRefreshResponse {
  fetched: number;
  archived: number;
  error: string | null;
  last_refreshed_at: string | null;
}

export interface PipelineProcessRetry {
  name: string;
  attempts: number;
}

export interface PipelineProgress {
  total_processes: number;
  completed: number;
  running: number;
  failed: number;
  cached: number;
  percent_complete: number;
  retries?: PipelineProcessRetry[];
}

export interface PipelineProcess {
  id: number;
  process_name: string;
  task_id: string | null;
  status: PipelineProcessStatus;
  exit_code: number | null;
  cpu_usage: number | null;
  memory_peak_gb: number | null;
  duration_seconds: number | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface PipelineRun {
  id: number;
  pipeline_key: string | null;
  pipeline_name: string;
  pipeline_version: string | null;
  experiment: ExperimentReference | null;
  submitted_by: UserSummary | null;
  status: PipelineRunStatus;
  parameters: Record<string, unknown> | null;
  input_files: Record<string, unknown> | null;
  output_files: Record<string, unknown> | null;
  progress: PipelineProgress | null;
  cost_estimate: number | null;
  error_message: string | null;
  failure_reason: "oom" | "preemption_exhausted" | "task_error" | null;
  work_dir: string | null;
  slurm_job_id: string | null;
  // Backend-neutral compute handle + provider-specifics disclosure (BAL Phase 4).
  compute_job_ref: string | null;
  provider_metadata: Record<string, unknown> | null;
  actual_cost: number | null;
  reference_genome: string | null;
  alignment_algorithm: string | null;
  resume_from_run_id: number | null;
  review_verdict: ReviewVerdict | null;
  custom_pipeline_version_id: number | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
}

export interface PipelineRunDetail extends PipelineRun {
  processes: PipelineProcess[];
  samples: Array<{
    id: number;
    external_id: string | null;
    organism: string | null;
  }>;
  /** The exact sheet handed to Nextflow. Null for runs launched before the
   *  snapshot existed; nothing is reconstructed in its place. */
  samplesheet_csv?: string | null;
  /** The same sheet with a bioaf_sample_uid column beside it, for checking by
   *  eye which asset each row stood for. Never what was submitted. */
  samplesheet_snapshot_csv?: string | null;
  /** The stated design that produced it, stamped with who set each value. */
  samplesheet_design?: RunSamplesheetDesign | null;
}

export interface PipelineRunListResponse {
  runs: PipelineRun[];
  total: number;
  page: number;
  page_size: number;
}

export interface PipelineRunLaunchRequest {
  pipeline_key: string;
  experiment_id: number;
  sample_ids?: number[] | null;
  parameters: Record<string, unknown>;
  /** What the scientist stated per sample, keyed by sample id then column. A
   *  first-class field rather than a key inside `parameters`, which is emitted
   *  verbatim onto the Nextflow command line. */
  sample_values?: Record<string, Record<string, string>>;
  resume_from_run_id?: number | null;
  reference_genome?: string | null;
  alignment_algorithm?: string | null;
  drop_samples_without_files?: boolean;
  /** The samplesheet columns declared on screen, for a pipeline that publishes
   *  no contract. Omitted entirely when the editor has not been read yet, which
   *  is not the same as an empty list: absent means "whatever is saved", `[]`
   *  means the scientist cleared it and wants the generic sheet. Sending it does
   *  NOT save it; that is the separate "Save for next time" button. */
  columns?: DeclaredColumn[];
}

export interface PipelineRunCompareResponse {
  runs: PipelineRun[];
  parameter_diffs: Record<string, Record<string, unknown>>;
}

export interface PipelineAddRequest {
  name: string;
  source_url: string;
  version?: string | null;
  description?: string | null;
}

export type CustomPipelineCodeSource = "github_repo" | "code_blob" | "inline";

export type CustomPipelineVariableType = "string" | "number" | "boolean" | "reference";

export type ReferenceParameterCategory =
  | "genome"
  | "annotation"
  | "index"
  | "atlas"
  | "markers"
  | "other"
  | "any";

export type CustomPipelineVersionStatus = "active" | "deprecated";

export type CustomPipelineVersionTrigger = "user" | "environment_cascade";

export interface CustomPipelineVariable {
  id: number;
  variable_name: string;
  default_value: string | null;
  variable_type: CustomPipelineVariableType;
  reference_category?: ReferenceParameterCategory | null;
  is_required: boolean;
}

export interface CustomPipelineVariableDefinition {
  variable_name: string;
  default_value: string | null;
  variable_type: CustomPipelineVariableType;
  reference_category?: ReferenceParameterCategory | null;
  is_required: boolean;
}

export interface CustomPipelineVersion {
  id: number;
  version_number: number;
  code_source_type: CustomPipelineCodeSource;
  github_repo_id: number | null;
  code_content: string | null;
  entrypoint_command: string;
  environment_version_id: number;
  cpu_request: string;
  memory_request: string;
  log_file_path: string | null;
  version_trigger: CustomPipelineVersionTrigger;
  status: CustomPipelineVersionStatus;
  created_by_user_id: number;
  created_at: string | null;
  variables: CustomPipelineVariable[];
  qc_template: string | null;
  qc_config_json: Record<string, unknown> | null;
}

export interface CustomPipeline {
  id: number;
  name: string;
  description: string | null;
  pipeline_key: string;
  created_by_user_id: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface CustomPipelineDetail extends CustomPipeline {
  versions: CustomPipelineVersion[];
}

export interface CustomPipelineCreateRequest {
  name: string;
  description?: string | null;
}

export interface CustomPipelineUpdateRequest {
  name?: string | null;
  description?: string | null;
}

export interface CustomPipelineEnvironmentSummary {
  environment_id: number;
  environment_name: string;
  version_id: number;
  version_number: number;
  build_number: number;
  image_uri: string | null;
}

export interface CustomPipelineRunOverview {
  pipeline_id: number;
  pipeline_name: string;
  pipeline_key: string;
  version_id: number;
  version_number: number;
  code_source_type: CustomPipelineCodeSource;
  github_repo_id: number | null;
  entrypoint_command: string;
  cpu_request: string;
  memory_request: string;
  log_file_path: string | null;
  environment: CustomPipelineEnvironmentSummary | null;
  variables: CustomPipelineVariable[];
}

export interface CustomPipelineVersionCreateRequest {
  code_source_type: CustomPipelineCodeSource;
  code_content?: string | null;
  github_repo_id?: number | null;
  entrypoint_command: string;
  environment_version_id: number;
  cpu_request?: string;
  memory_request?: string;
  log_file_path?: string | null;
  variables: CustomPipelineVariableDefinition[];
  qc_template?: string | null;
  qc_config_json?: Record<string, unknown> | null;
}

export type ReviewVerdict = "approved" | "approved_with_caveats" | "rejected" | "revision_requested";

/** A sample's QC verdict within a review: its own vocabulary, not a run-review verdict. */
export type SampleVerdict = "pass" | "warning" | "fail";

type GeneratedReviewCreate = paths["/api/pipeline-runs/{run_id}/reviews"]["post"]["requestBody"]["content"]["application/json"];
type GeneratedSampleVerdictEntry = NonNullable<GeneratedReviewCreate["sample_verdicts"]>[string];

/**
 * `G` with the fields named in `N` narrowed to `N`'s value types. Which fields are required (and
 * readonly) stays exactly as generated: the mapped type is homomorphic over `Pick<G, ...>`, so a
 * field the schema makes required becomes required here too. type-contracts holds each narrowed
 * request assignable to its generated one, so a narrowing cannot widen what the schema allows.
 */
type Narrow<G, N extends { [K in keyof G]?: unknown }> = Omit<G, keyof N> & {
  [K in keyof Pick<G, keyof N & keyof G>]: K extends keyof N ? N[K] : never;
};

/** One sample's verdict in a review request: the generated entry with the verdict values the backend accepts. */
export type SampleVerdictEntry = Narrow<GeneratedSampleVerdictEntry, { verdict: SampleVerdict }>;

/**
 * POST /api/pipeline-runs/{id}/reviews. The generated request with its two open `verdict`
 * strings narrowed to the values the backend validators accept. Sample verdicts are keyed by sample id.
 */
export type PipelineRunReviewCreate = Narrow<
  GeneratedReviewCreate,
  { verdict: ReviewVerdict; sample_verdicts: Record<string, SampleVerdictEntry> | null }
>;

/**
 * A review as the review API emits it. Generated transport: `verdict` is an open string (the
 * badge labels unknown values), `reviewer` is nullable in the declared schema (a stored review
 * always has one today), `sample_verdicts_json` is the stored dictionary, not validated data,
 * and `recommended_exclusions` is an untyped list in the schema.
 */
export type PipelineRunReview = PipelineRunReviewListResponse["reviews"][number];

/** GET /api/pipeline-runs/{run_id}/reviews, bound to the operation; the active read and create are held equal in type-contracts. */
export type PipelineRunReviewListResponse = paths["/api/pipeline-runs/{run_id}/reviews"]["get"]["responses"][200]["content"]["application/json"];

/** One value a scientist stated, with the authorship the run keeps. A value
 *  that did not change keeps its original author, so re-saving a design does
 *  not quietly reassign who said what. */
export interface StatedValue {
  value: string;
  set_by: string | null;
  set_at: string | null;
}

/** The design a run used, snapshotted at launch. A mapping edited afterwards
 *  must not rewrite the history of a run that already used it. */
export interface RunSamplesheetDesign {
  values: Record<string, Record<string, StatedValue>>;
  bindings: Record<string, StatedValue>;
}
