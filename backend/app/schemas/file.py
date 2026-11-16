from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from app.schemas.experiment import UserSummary


class FileUploadInitiate(BaseModel):
    filename: str
    expected_size_bytes: int | None = None
    expected_md5: str | None = None
    project_id: int | None = None
    experiment_id: int | None = None
    sample_ids: list[int] = []
    is_global: bool = False
    # The caller's identifier for the file (1-255 characters), kept exactly; used at completion.
    external_id: str | None = Field(default=None, min_length=1, max_length=255)


class FileUploadComplete(BaseModel):
    upload_id: str
    actual_md5: str = ""


class PipelineRunRef(BaseModel):
    id: int
    pipeline_name: str
    launcher: UserSummary | None = None


class ComputeSessionRef(BaseModel):
    id: int
    # "work_node" for SSH/GCE sessions; "notebook" for in-browser RStudio/Jupyter
    kind: str
    # For notebooks only: "rstudio" or "jupyter"; null for work nodes
    notebook_type: str | None = None
    launcher: UserSummary | None = None


class FileProvenance(BaseModel):
    project_id: int | None = None
    project_name: str | None = None
    experiment_id: int | None = None
    experiment_name: str | None = None
    sample_labels: list[str] = []
    pipeline_run: PipelineRunRef | None = None
    compute_session: ComputeSessionRef | None = None
    creator: UserSummary | None = None


class SampleReference(BaseModel):
    """A linked sample, with its experiment: sample external IDs are unique only per experiment."""

    sample_id: int
    external_id: str | None = None
    experiment_id: int
    experiment_external_id: str | None = None


class FileVerificationOut(BaseModel):
    """Checksum verification of the file's stored bytes. Expected and observed values are separate:
    an expected checksum is what a caller or source asserted; an observed one bioAF read itself."""

    status: str
    algorithm: str | None = None
    expected_checksum: str | None = None
    observed_checksum: str | None = None
    method: str | None = None
    verified_at: datetime | None = None
    bytes_processed: int | None = None
    total_bytes: int | None = None
    error_code: str | None = None
    retryable: bool | None = None


class FileResponse(BaseModel):
    id: int
    filename: str
    gcs_uri: str
    size_bytes: int | None
    md5_checksum: str | None
    file_type: str
    tags: list[str] = []
    uploader: UserSummary | None = None
    project_id: int | None = None
    experiment_id: int | None = None
    sample_ids: list[int] = []
    source_type: str = "upload"
    source_pipeline_run_id: int | None = None
    source_notebook_session_id: int | None = None
    sha256_checksum: str | None = None
    artifact_type: str | None = None
    storage_deleted: bool = False
    is_global: bool = False
    upload_timestamp: datetime
    created_at: datetime
    provenance: FileProvenance | None = None
    # The caller's identifier for this file, or null when none was assigned (01B.5). Each parent
    # external ID belongs to the parent ID beside it.
    external_id: str | None = None
    project_external_id: str | None = None
    experiment_external_id: str | None = None
    samples: list[SampleReference] = []
    # When the stored bytes were first verified available; null when not (yet) established.
    ready_at: datetime | None = None
    verification: FileVerificationOut | None = None

    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def storage_uri(self) -> str:
        """Backend-neutral alias of the object-store URI. gcs_uri is the retained
        legacy mirror on the wire; clients should read storage_uri. Remove gcs_uri
        from the response once no client reads it."""
        return self.gcs_uri


class FileListResponse(BaseModel):
    files: list[FileResponse]
    total: int
    page: int
    page_size: int


class FileLinkRequest(BaseModel):
    project_id: int | None = None
    experiment_id: int | None = None
    sample_id: int | None = None
    pipeline_run_id: int | None = None


class FileUploadInitiateResponse(BaseModel):
    upload_id: str
    signed_url: str
    gcs_uri: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def storage_uri(self) -> str:
        """Neutral alias of gcs_uri (retained legacy mirror); read storage_uri."""
        return self.gcs_uri
