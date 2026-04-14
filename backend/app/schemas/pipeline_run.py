from datetime import datetime

from pydantic import BaseModel


class ExperimentSummary(BaseModel):
    id: int
    name: str


class UserSummary(BaseModel):
    id: int
    name: str | None = None
    email: str


class SampleSummary(BaseModel):
    id: int
    sample_id_unique: str | None = None
    organism: str | None = None


class PipelineProgress(BaseModel):
    total_processes: int
    completed: int
    running: int
    failed: int
    cached: int
    percent_complete: float


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
    resume_from_run_id: int | None = None
    reference_genome: str | None = None
    alignment_algorithm: str | None = None


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
    k8s_job_name: str | None = None
    k8s_namespace: str | None = None
    k8s_pod_name: str | None = None
    actual_cost: float | None = None
    reference_genome: str | None = None
    alignment_algorithm: str | None = None
    resume_from_run_id: int | None = None
    review_verdict: str | None = None
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


class PipelineRunDetailResponse(PipelineRunResponse):
    processes: list[PipelineProcessResponse] = []
    samples: list[SampleSummary] = []


class PipelineRunCompareRequest(BaseModel):
    run_ids: list[int]


class PipelineRunCompareResponse(BaseModel):
    runs: list[PipelineRunResponse]
    parameter_diffs: dict


class ProvenanceExportRequest(BaseModel):
    format: str = "json"
