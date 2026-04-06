from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator


class EventTriggerConfig(BaseModel):
    file_types: list[str]
    project_filter: list[int] | None = None
    experiment_filter: list[int] | None = None
    batching_window_minutes: int = 15
    trigger_on: Literal["file_upload", "sample_complete"] = "file_upload"
    required_file_types: list[str] | None = None

    @field_validator("file_types")
    @classmethod
    def validate_file_types(cls, v: list[str]) -> list[str]:
        if len(v) == 0:
            raise ValueError("at least one file type is required")
        return v

    @field_validator("batching_window_minutes")
    @classmethod
    def validate_window(cls, v: int) -> int:
        if v < 0:
            raise ValueError("batching_window_minutes must be non-negative")
        return v


class ScheduleTriggerConfig(BaseModel):
    cron_expression: str
    timezone: str = "UTC"
    file_types: list[str]
    project_filter: list[int] | None = None
    min_files_to_trigger: int = 1

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("cron_expression must not be empty")
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError("cron_expression must have exactly 5 fields")
        return v


class BudgetTriggerConfig(BaseModel):
    require_approval_on_budget_warning: bool = True
    auto_queue_when_over_budget: bool = True


class PipelineTriggerCreate(BaseModel):
    pipeline_id: int
    trigger_mode: Literal["manual", "event_driven", "scheduled"]
    event_config: EventTriggerConfig | None = None
    schedule_config: ScheduleTriggerConfig | None = None
    parameter_defaults: dict = {}
    budget_config: BudgetTriggerConfig = BudgetTriggerConfig()
    enabled: bool = True

    @field_validator("event_config")
    @classmethod
    def validate_event_config(cls, v: EventTriggerConfig | None, info) -> EventTriggerConfig | None:
        if info.data.get("trigger_mode") == "event_driven" and v is None:
            raise ValueError("event_config is required when trigger_mode is event_driven")
        return v

    @field_validator("schedule_config")
    @classmethod
    def validate_schedule_config(cls, v: ScheduleTriggerConfig | None, info) -> ScheduleTriggerConfig | None:
        if info.data.get("trigger_mode") == "scheduled" and v is None:
            raise ValueError("schedule_config is required when trigger_mode is scheduled")
        return v


class PipelineTriggerUpdate(BaseModel):
    trigger_mode: Literal["manual", "event_driven", "scheduled"] | None = None
    event_config: EventTriggerConfig | None = None
    schedule_config: ScheduleTriggerConfig | None = None
    parameter_defaults: dict | None = None
    budget_config: BudgetTriggerConfig | None = None
    enabled: bool | None = None


class PipelineTriggerResponse(BaseModel):
    id: int
    pipeline_id: int
    organization_id: int
    trigger_mode: str
    event_config: dict | None = None
    schedule_config: dict | None = None
    parameter_defaults: dict
    budget_config: dict
    enabled: bool
    created_by: int
    created_at: datetime
    updated_at: datetime
    runs_triggered_7d: int | None = None
    runs_triggered_30d: int | None = None
    next_scheduled_run: datetime | None = None

    model_config = {"from_attributes": True}


class BudgetCheckResult(BaseModel):
    estimated_cost: float
    confidence_interval_pct: float
    current_month_spend: float
    queued_running_cost: float
    projected_total: float
    monthly_budget: float
    decision: Literal["within_budget", "might_exceed", "will_exceed", "budget_exhausted"]


class CostEstimateResponse(BaseModel):
    pipeline_name: str
    estimated_cost: float
    confidence_interval_pct: float
    based_on_history_count: int
    budget_check: BudgetCheckResult
