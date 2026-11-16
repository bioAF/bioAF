from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.file import FileVerificationOut, SampleReference


class FileOut(BaseModel):
    """Read-only file metadata. gcs_uri is intentionally excluded; bytes do
    not flow through the public API in v1."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    size_bytes: int | None
    md5_checksum: str | None
    sha256_checksum: str | None
    file_type: str | None
    source_type: str | None
    project_id: int | None
    experiment_id: int | None
    sample_ids: list[int] = []
    tags: dict | list | None = None
    created_at: datetime
    external_id: str | None = None
    project_external_id: str | None = None
    experiment_external_id: str | None = None
    samples: list[SampleReference] = []
    ready_at: datetime | None = None
    verification: FileVerificationOut | None = None


class FileListOut(BaseModel):
    items: list[FileOut]
    next_cursor: str | None = None
