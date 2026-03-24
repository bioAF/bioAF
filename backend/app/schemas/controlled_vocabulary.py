from datetime import datetime

from pydantic import BaseModel, field_validator


CONTROLLED_FIELD_NAMES = [
    "molecule_type",
    "library_prep_method",
    "library_layout",
    "instrument_model",
    "instrument_platform",
    "quality_score_encoding",
    "reference_genome",
    "alignment_algorithm",
    "design_type",
]


class ControlledVocabularyCreate(BaseModel):
    field_name: str
    allowed_value: str
    display_label: str | None = None
    display_order: int | None = None
    is_default: bool | None = None

    @field_validator("field_name")
    @classmethod
    def validate_field_name(cls, v: str) -> str:
        if v not in CONTROLLED_FIELD_NAMES:
            raise ValueError(f"field_name must be one of: {', '.join(CONTROLLED_FIELD_NAMES)}")
        return v


class ControlledVocabularyUpdate(BaseModel):
    display_label: str | None = None
    display_order: int | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class ControlledVocabularyValueResponse(BaseModel):
    id: int
    value: str
    display_label: str | None
    display_order: int
    is_default: bool
    is_active: bool

    model_config = {"from_attributes": True}


class ControlledVocabularyResponse(BaseModel):
    field_name: str
    values: list[ControlledVocabularyValueResponse]


class ControlledVocabularyDetailResponse(BaseModel):
    id: int
    field_name: str
    allowed_value: str
    display_label: str | None
    display_order: int
    is_default: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ControlledVocabularyFieldsResponse(BaseModel):
    fields: list[str]
