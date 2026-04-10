from datetime import datetime

from pydantic import BaseModel


class AutoRunConfigCreate(BaseModel):
    pipeline_key: str
    parameters: dict = {}
    reference_genome: str | None = None
    alignment_algorithm: str | None = None
    delay_minutes: int = 0


class AutoRunConfigUpdate(BaseModel):
    parameters: dict | None = None
    reference_genome: str | None = None
    alignment_algorithm: str | None = None
    delay_minutes: int | None = None
    enabled: bool | None = None


class AutoRunConfigResponse(BaseModel):
    id: int
    experiment_id: int
    pipeline_key: str
    parameters: dict | None = None
    reference_genome: str | None = None
    alignment_algorithm: str | None = None
    delay_minutes: int
    enabled: bool
    configured_by_user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PendingAutoRunResponse(BaseModel):
    id: int
    auto_run_config_id: int
    experiment_id: int
    sample_id: int
    sample_completed_at: datetime
    scheduled_at: datetime
    status: str
    pipeline_run_id: int | None = None
    cancelled_reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
