from datetime import datetime

from pydantic import BaseModel, computed_field

from app.schemas.experiment import UserSummary
from app.schemas.file import FileResponse


class CellxgenePublishableFile(BaseModel):
    id: int
    filename: str
    gcs_uri: str
    size_bytes: int | None
    file_type: str
    project_name: str | None = None
    experiment_name: str | None = None
    sample_names: list[str] = []
    source_type: str = "upload"
    cellxgene_ready: bool = False
    cellxgene_status: str = "unknown"
    created_at: datetime

    @computed_field
    @property
    def storage_uri(self) -> str:
        """Neutral alias of gcs_uri (retained legacy mirror); read storage_uri."""
        return self.gcs_uri


class CellxgenePublishRequest(BaseModel):
    file_id: int
    experiment_id: int | None = None
    dataset_name: str


class CellxgenePublicationResponse(BaseModel):
    id: int
    dataset_name: str
    stable_url: str | None
    access_url: str | None
    status: str
    file: FileResponse | None = None
    experiment_id: int | None
    published_by: UserSummary | None = None
    published_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
