from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.pipeline_run_review import REVIEW_VERDICTS
from app.schemas.user_summary import UserSummary


class SampleVerdictEntry(BaseModel):
    verdict: str
    notes: str | None = None

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v: str) -> str:
        if v not in ("pass", "warning", "fail"):
            raise ValueError("Sample verdict must be 'pass', 'warning', or 'fail'")
        return v


class PipelineRunReviewCreate(BaseModel):
    verdict: str
    notes: str | None = None
    sample_verdicts: dict[str, SampleVerdictEntry] | None = None
    recommended_exclusions: list[int] | None = None

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v: str) -> str:
        if v not in REVIEW_VERDICTS:
            raise ValueError(f"verdict must be one of: {', '.join(REVIEW_VERDICTS)}")
        return v


# A reviewer is a user as other records reference them; one definition, the old name kept.
ReviewerSummary = UserSummary


class PipelineRunReviewResponse(BaseModel):
    id: int
    pipeline_run_id: int
    reviewer: ReviewerSummary | None = None
    verdict: str
    notes: str | None
    sample_verdicts_json: dict | None
    recommended_exclusions: list | None
    reviewed_at: datetime
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PipelineRunReviewListResponse(BaseModel):
    reviews: list[PipelineRunReviewResponse]
