from datetime import datetime

from pydantic import BaseModel

from app.schemas.experiment import UserSummary


class FileUploadInitiate(BaseModel):
    filename: str
    expected_size_bytes: int | None = None
    expected_md5: str | None = None
    experiment_id: int | None = None
    sample_ids: list[int] = []


class FileUploadComplete(BaseModel):
    upload_id: str
    actual_md5: str = ""


class FileResponse(BaseModel):
    id: int
    filename: str
    gcs_uri: str
    size_bytes: int | None
    md5_checksum: str | None
    file_type: str
    tags: list[str] = []
    uploader: UserSummary | None = None
    experiment_id: int | None = None
    upload_timestamp: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class FileListResponse(BaseModel):
    files: list[FileResponse]
    total: int
    page: int
    page_size: int


class FileLinkRequest(BaseModel):
    experiment_id: int | None = None
    sample_id: int | None = None
    pipeline_run_id: int | None = None


class FileUploadInitiateResponse(BaseModel):
    upload_id: str
    signed_url: str
    gcs_uri: str
