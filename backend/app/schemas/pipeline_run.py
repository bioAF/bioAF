from datetime import datetime

from pydantic import BaseModel

from app.schemas.samplesheet_mapping import SamplesheetPrefill


class ExperimentSummary(BaseModel):
    id: int
    name: str


class UserSummary(BaseModel):
    id: int
    name: str | None = None
    email: str


class SampleSummary(BaseModel):
    id: int
    external_id: str | None = None
    organism: str | None = None


class PipelineProcessRetry(BaseModel):
    name: str
    attempts: int


class PipelineProgress(BaseModel):
    total_processes: int
    completed: int
    running: int
    failed: int
    cached: int
    percent_complete: float
    retries: list[PipelineProcessRetry] | None = None


class PipelineProcessResponse(BaseModel):
    id: int
    process_name: str
    task_id: str | None = None
    status: str
    exit_code: int | None = None
    cpu_usage: float | None = None
    memory_peak_gb: float | None = None
    duration_seconds: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class PipelineRunLaunchRequest(BaseModel):
    pipeline_key: str
    experiment_id: int
    project_id: int | None = None
    sample_ids: list[int] | None = None
    parameters: dict = {}
    # Samplesheet values the scientist stated, keyed by sample id then column:
    # ``{"12": {"group": "gut"}}``. Columns describing experimental design have
    # no source bioAF may read, and guessing one produces a run that completes
    # green and is scientifically wrong.
    #
    # A FIRST-CLASS field rather than a key inside ``parameters``, because
    # ``parameters`` is emitted verbatim onto the Nextflow command line (one
    # ``--key value`` per entry), so this would arrive as a bogus
    # ``--sample_values`` argument.
    sample_values: dict[str, dict[str, str]] = {}
    resume_from_run_id: int | None = None
    reference_genome: str | None = None
    alignment_algorithm: str | None = None
    # When a FASTQ-consuming pipeline has selected samples that lack linked input
    # files, the launch is rejected with SamplesMissingFilesError. Set this to
    # drop those samples and run with the rest instead.
    drop_samples_without_files: bool = False
    # By default a previous pipeline/notebook run's output files are NOT fed back
    # in as inputs (they would compound the dataset every run). Set this to opt
    # in to using derived files as inputs.
    include_derived_inputs: bool = False


class PipelineRunResponse(BaseModel):
    id: int
    pipeline_key: str | None = None
    pipeline_name: str
    pipeline_version: str | None = None
    experiment: ExperimentSummary | None = None
    project_id: int | None = None
    submitted_by: UserSummary | None = None
    status: str
    parameters: dict | None = None
    input_files: list[int] | dict | None = None
    output_files: dict | None = None
    progress: PipelineProgress | None = None
    cost_estimate: float | None = None
    error_message: str | None = None
    failure_reason: str | None = None
    work_dir: str | None = None
    slurm_job_id: str | None = None
    # Backend-neutral compute fields (BAL Phase 4). compute_job_ref is the opaque
    # compute handle; provider_metadata is the backend-specifics disclosure. The
    # former top-level k8s_job_name/namespace/pod_name were removed here (they now
    # live inside provider_metadata); the DB columns persist until a later drop
    # migration once internal readers are migrated.
    compute_job_ref: str | None = None
    provider_metadata: dict | None = None
    actual_cost: float | None = None
    reference_genome: str | None = None
    alignment_algorithm: str | None = None
    resume_from_run_id: int | None = None
    review_verdict: str | None = None
    custom_pipeline_version_id: int | None = None
    retry_count: int = 0
    reviewed_by_user_id: int | None = None
    reviewed_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class PipelineRunListResponse(BaseModel):
    runs: list[PipelineRunResponse]
    total: int
    page: int
    page_size: int


class StatedValue(BaseModel):
    """One value a scientist stated, and who stated it.

    Whoever fills the design grid is often not whoever launches, so a launcher
    stamp alone names the wrong person for the value that turned out wrong.
    """

    value: str
    set_by: str | None = None
    set_at: str | None = None


class RunSamplesheetDesign(BaseModel):
    values: dict[str, dict[str, StatedValue]] = {}
    bindings: dict[str, StatedValue] = {}


class PipelineRunDetailResponse(PipelineRunResponse):
    processes: list[PipelineProcessResponse] = []
    samples: list[SampleSummary] = []
    # The exact sheet this run was handed, kept rather than re-derived: today's
    # samples, files and mapping are not what the run received. Null for runs
    # launched before the snapshot existed, and nothing is reconstructed.
    samplesheet_csv: str | None = None
    samplesheet_design: RunSamplesheetDesign | None = None
    # The same sheet with a `bioaf_sample_uid` column beside it. Its whole point
    # is manual verification: a person can check by eye which asset each row
    # stood for. Never what was submitted, and null for runs launched before the
    # record existed.
    samplesheet_snapshot_csv: str | None = None


class PipelineRunCompareRequest(BaseModel):
    run_ids: list[int]


class PipelineRunCompareResponse(BaseModel):
    runs: list[PipelineRunResponse]
    parameter_diffs: dict


class ProvenanceExportRequest(BaseModel):
    format: str = "json"


class PipelineRunPreflightResponse(BaseModel):
    """Whether a launch would succeed, asked before anything is created.

    ``code`` and ``details`` mirror the domain error the launch would raise, so
    the dialog can render the same explanation the API would have returned:
    which columns are missing, which samples lack them, and what the pipeline
    wants instead when it does not read sequences at all.
    """

    can_launch: bool
    code: str | None = None
    reason: str | None = None
    details: dict = {}
    # The sheet this run would submit: ``columns``, ``rows`` (each naming the
    # sample it belongs to) and the exact ``csv``. Produced by the generator that
    # feeds Nextflow rather than by a second code path, so the review step cannot
    # show a sheet other than the one about to run.
    samplesheet: dict = {}
    # The columns an entry grid must collect, and how to render each. Derived
    # from the same computation as the block above, so the questions asked and
    # the refusal given cannot disagree.
    per_sample_inputs: list[dict] = []
    # What a saved design would contribute, the rung it came from, and the
    # selected samples it does not name. An offer to fill the grid with, never
    # applied to the sheet above: that is what keeps a design from carrying
    # silently onto a sample set it was not set for.
    prefill: SamplesheetPrefill = SamplesheetPrefill()
    # Whether this pipeline's sheet is one a scientist DECLARES (it publishes no
    # contract and no tailored generator owns it), and the vocabulary its columns
    # may be bound against: the file types and custom fields these samples
    # actually carry, so a binding is chosen from what exists rather than typed
    # from memory.
    declaration: dict = {}
