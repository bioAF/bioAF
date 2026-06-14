from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from app.schemas.experiment import UserSummary


class LabDocumentTagResponse(BaseModel):
    id: int
    name: str


class LabDocumentNoteCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=10000)


class LabDocumentNoteResponse(BaseModel):
    id: int
    body: str
    user: UserSummary | None = None
    created_at: datetime
    deleted: bool = False


class LabDocumentVersionResponse(BaseModel):
    version_number: int
    file_name: str
    file_size_bytes: int | None = None
    md5_checksum: str | None = None
    change_note: str | None = None
    uploaded_by: UserSummary | None = None
    uploaded_at: datetime


class LabDocumentResponse(BaseModel):
    id: int
    title: str
    description: str | None = None
    file_name: str
    current_version: int
    file_size_bytes: int | None = None
    mime_type: str | None = None
    md5_checksum: str | None = None
    is_archived: bool
    tags: list[LabDocumentTagResponse] = []
    created_by: UserSummary | None = None
    created_at: datetime
    updated_at: datetime


class LabDocumentListResponse(BaseModel):
    documents: list[LabDocumentResponse]
    total: int
    page: int
    page_size: int


class LabDocumentUploadUrlRequest(BaseModel):
    file_name: str = Field(..., max_length=500)
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


class LabDocumentUploadUrlResponse(BaseModel):
    upload_token: str
    signed_url: str
    gcs_uri: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def storage_uri(self) -> str:
        """Neutral alias of gcs_uri (retained legacy mirror); read storage_uri."""
        return self.gcs_uri


class LabDocumentCreate(BaseModel):
    upload_token: str
    title: str = Field(..., max_length=500)
    description: str | None = None
    tag_ids: list[int] = []


class LabDocumentUrlImportRequest(BaseModel):
    # ``str`` (not HttpUrl) so the service can validate the scheme and return a
    # friendly 400; an unparseable URL still fails there.
    url: str = Field(..., min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    tag_ids: list[int] = []


class LabDocumentUrlImportResponse(BaseModel):
    id: int
    status: str
    document_id: int | None = None
    error_message: str | None = None


class LabDocumentVersionCreate(BaseModel):
    upload_token: str
    change_note: str | None = Field(default=None, max_length=500)


class LabDocumentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    tag_ids: list[int] | None = None


class LabDocumentTagCreate(BaseModel):
    name: str = Field(..., max_length=100)
