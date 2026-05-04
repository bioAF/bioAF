from datetime import datetime

from pydantic import BaseModel

from app.schemas.experiment import UserSummary


class FileUploadInitiate(BaseModel):
    filename: str
    expected_size_bytes: int | None = None
    expected_md5: str | None = None
    project_id: int | None = None
    experiment_id: int | None = None
    sample_ids: list[int] = []
    is_global: bool = False


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

    model_config = {"from_attributes": True}


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
