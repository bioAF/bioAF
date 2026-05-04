from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.reference_dataset import REFERENCE_CATEGORIES, REFERENCE_SCOPES


# --- Request schemas ---


class ReferenceDatasetFileCreate(BaseModel):
    filename: str
    gcs_uri: str
    size_bytes: int | None = None
    md5_checksum: str | None = None
    file_type: str | None = None


class ReferenceDatasetCreate(BaseModel):
    name: str
    category: str
    scope: str
    version: str
    source_url: str | None = None
    gcs_prefix: str
    total_size_bytes: int | None = None
    md5_manifest_json: dict | None = None
    files: list[ReferenceDatasetFileCreate] = []

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in REFERENCE_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(REFERENCE_CATEGORIES)}")
        return v

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in REFERENCE_SCOPES:
            raise ValueError(f"scope must be one of: {', '.join(REFERENCE_SCOPES)}")
        return v


class ReferenceDeprecateRequest(BaseModel):
    deprecation_note: str
    superseded_by_id: int | None = None


# --- Upload (resumable session) schemas ---


class ReferenceUploadFileSpec(BaseModel):
    filename: str
    size_bytes: int
    content_type: str | None = None
    md5_checksum: str | None = None  # advanced path: client-supplied md5 for verify


class ReferenceUploadInitRequest(BaseModel):
    name: str
    category: str
    scope: str
    version: str
    source_url: str | None = None
    description: str | None = None
    files: list[ReferenceUploadFileSpec]

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in REFERENCE_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(REFERENCE_CATEGORIES)}")
        return v

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in REFERENCE_SCOPES:
            raise ValueError(f"scope must be one of: {', '.join(REFERENCE_SCOPES)}")
        return v


class ReferenceUploadSlot(BaseModel):
    filename: str
    session_url: str
    expires_at: datetime


class ReferenceUploadInitResponse(BaseModel):
    reference_id: int
    gcs_prefix: str
    uploads: list[ReferenceUploadSlot]


# --- Import-from-URL (GKE Job) schemas — spec §3 ---


VALID_EXTRACT_MODES = ["none", "gzip", "tar", "tar.gz"]


class ReferenceImportRequest(BaseModel):
    name: str
    category: str
    scope: str
    version: str
    source_url: str
    source_md5_url: str | None = None
    auth_header: str | None = None
    extract: str = "none"
    expected_files: list[str] | None = None
    description: str | None = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in REFERENCE_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(REFERENCE_CATEGORIES)}")
        return v

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: str) -> str:
        if v not in REFERENCE_SCOPES:
            raise ValueError(f"scope must be one of: {', '.join(REFERENCE_SCOPES)}")
        return v

    @field_validator("extract")
    @classmethod
    def validate_extract(cls, v: str) -> str:
        if v not in VALID_EXTRACT_MODES:
            raise ValueError(f"extract must be one of: {', '.join(VALID_EXTRACT_MODES)}")
        return v


class ReferenceImportStartResponse(BaseModel):
    reference_id: int
    import_job_id: str
    status: str


class ReferenceImportStatusResponse(BaseModel):
    reference_id: int
    status: str
    progress_pct: int | None = None
    bytes_downloaded: int | None = None
    total_bytes: int | None = None
    error_message: str | None = None
    import_job_id: str | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReferenceImportProgressUpdate(BaseModel):
    """Internal callback body — written by the importer container."""

    status: str
    progress_pct: int | None = None
    bytes_downloaded: int | None = None
    total_bytes: int | None = None
    error_message: str | None = None


# --- Response schemas ---


class ReferenceDatasetFileResponse(BaseModel):
    id: int
    filename: str
    gcs_uri: str
    size_bytes: int | None
    md5_checksum: str | None
    file_type: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserSummary(BaseModel):
    id: int
    name: str | None = None
    email: str

    model_config = {"from_attributes": True}


class ReferenceDatasetResponse(BaseModel):
    id: int
    organization_id: int
    name: str
    category: str
    scope: str
    version: str
    source_url: str | None
    gcs_prefix: str
    total_size_bytes: int | None
    file_count: int | None
    status: str
    deprecation_note: str | None
    superseded_by_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReferenceDatasetDetailResponse(ReferenceDatasetResponse):
    files: list[ReferenceDatasetFileResponse] = []
    uploaded_by: UserSummary | None = None
    approved_by: UserSummary | None = None


class ReferenceDatasetListResponse(BaseModel):
    references: list[ReferenceDatasetResponse]
    total: int


# --- Impact schemas ---


class ImpactPipelineRun(BaseModel):
    pipeline_run_id: int
    pipeline_name: str
    pipeline_version: str | None
    experiment_id: int | None
    experiment_name: str | None
    status: str
    review_verdict: str | None = None
    completed_at: datetime | None


class ImpactSummary(BaseModel):
    reference_dataset_id: int
    total_pipeline_runs: int
    total_experiments: int
    pipeline_runs: list[ImpactPipelineRun]
