from datetime import datetime

from pydantic import BaseModel

from app.schemas.experiment import UserSummary


class DatasetExperimentSummary(BaseModel):
    experiment_id: int
    experiment_name: str
    status: str
    organism: str | None = None
    tissue: str | None = None
    molecule_type: str | None = None
    instrument_model: str | None = None
    review_status: str | None = None
    sample_count: int = 0
    file_count: int = 0
    total_size_bytes: int = 0
    pipeline_run_count: int = 0
    has_qc_dashboard: bool = False
    has_cellxgene: bool = False
    owner: UserSummary | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DatasetFilterOptions(BaseModel):
    organisms: list[str] = []


class DatasetSearchResult(BaseModel):
    experiments: list[DatasetExperimentSummary]
    total: int
    page: int
    page_size: int
