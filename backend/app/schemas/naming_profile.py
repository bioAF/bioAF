"""Pydantic schemas for the Naming Profile API.

The redesign moved the feature from a closed-enum-of-field-names model to a
template-driven, open vocabulary one. The only closed enum remaining is the
three field *types* (`string`, `number`, `date`), inherited from the
Experiment Template feature. Every other field name is user-defined or
template-defined.

See local/Naming Profiles/redesign-plan.md and the ADR proposal in the same
directory for the rationale.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# Closed enums. Field *types* are bounded by the Experiment Template feature;
# date formats are bounded by the three pre-built options. Field *names* are
# explicitly NOT a closed enum -- that was the failure mode of the original
# design.
FieldType = Literal["string", "number", "date"]
DateFormat = Literal["YYYYMMDD", "YYYY-MM-DD", "YYMMDD"]
Delimiter = Literal["_", "-"]

IDENTIFIER_REGEX = re.compile(r"^[A-Za-z]{1,4}$")
PADDING_MIN = 0
PADDING_MAX = 3
PADDING_DEFAULT = 2


class SegmentDefinition(BaseModel):
    """One parseable unit of a filename, as authored in a Naming Profile.

    The segment shape on disk follows from `field_type`:

    - `number` -> `<identifier><zero-padded integer>`, e.g. `SMP0042`.
    - `string` -> `<identifier><inner-separator><value>`, e.g. `req-bmills`.
      The inner separator is the opposite of the profile's delimiter.
    - `date`   -> one of three pre-built formats; no identifier.

    `position` is the display order in the wizard; **the parser does not
    use it**. Segments self-identify via `identifier` (or, for dates, by
    digit pattern).
    """

    position: int = Field(ge=0)
    identifier: str | None = None
    field_name: str = Field(min_length=1, max_length=64)
    field_type: FieldType
    padding: int | None = None
    date_format: DateFormat | None = None
    is_system_chip: bool = False

    @field_validator("identifier")
    @classmethod
    def _identifier_charset(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not IDENTIFIER_REGEX.match(v):
            raise ValueError("identifier must be 1-4 ASCII letters [A-Za-z]")
        return v

    @field_validator("padding")
    @classmethod
    def _padding_range(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if not (PADDING_MIN <= v <= PADDING_MAX):
            raise ValueError(f"padding must be between {PADDING_MIN} and {PADDING_MAX}")
        return v

    @model_validator(mode="after")
    def _shape_consistency(self) -> "SegmentDefinition":
        if self.field_type == "date":
            if self.identifier is not None:
                raise ValueError("date segments must not have an identifier")
            if self.date_format is None:
                raise ValueError("date segments require a date_format")
            if self.padding is not None:
                raise ValueError("date segments must not have padding")
        else:
            if self.identifier is None:
                raise ValueError(f"{self.field_type} segments require an identifier")
            if self.date_format is not None:
                raise ValueError(f"{self.field_type} segments must not have date_format")
            if self.field_type == "string" and self.padding is not None:
                raise ValueError("string segments must not have padding")
        return self


class NamingProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    delimiter: Delimiter = "_"
    strip_extension: bool = True
    segments: list[SegmentDefinition]
    experiment_template_id: int | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be empty")
        return v

    @field_validator("segments")
    @classmethod
    def _at_least_one_segment(cls, v: list[SegmentDefinition]) -> list[SegmentDefinition]:
        if not v:
            raise ValueError("at least one segment is required")
        return v

    @model_validator(mode="after")
    def _no_duplicate_identifiers(self) -> "NamingProfileCreate":
        seen = set()
        for seg in self.segments:
            if seg.identifier is None:
                continue
            key = seg.identifier.casefold()
            if key in seen:
                raise ValueError(f"identifier '{seg.identifier}' is used by more than one segment")
            seen.add(key)
        date_segments = [s for s in self.segments if s.field_type == "date"]
        if len(date_segments) > 1:
            raise ValueError("a naming profile may have at most one date segment")
        return self


class NamingProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    delimiter: Delimiter | None = None
    strip_extension: bool | None = None
    segments: list[SegmentDefinition] | None = None
    experiment_template_id: int | None = None

    @model_validator(mode="after")
    def _segment_constraints(self) -> "NamingProfileUpdate":
        if self.segments is None:
            return self
        if not self.segments:
            raise ValueError("at least one segment is required")
        seen = set()
        for seg in self.segments:
            if seg.identifier is None:
                continue
            key = seg.identifier.casefold()
            if key in seen:
                raise ValueError(f"identifier '{seg.identifier}' is used by more than one segment")
            seen.add(key)
        date_segments = [s for s in self.segments if s.field_type == "date"]
        if len(date_segments) > 1:
            raise ValueError("a naming profile may have at most one date segment")
        return self


class NamingProfileResponse(BaseModel):
    id: int
    organization_id: int
    name: str
    description: str | None
    delimiter: str
    strip_extension: bool
    segments: list[SegmentDefinition]
    experiment_template_id: int | None
    status: str
    created_by: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NamingProfileTestRequest(BaseModel):
    """Test an unsaved profile against one or more filenames.

    The profile body is sent inline so the wizard can preview parse
    behavior before saving.
    """

    filenames: list[str]
    delimiter: Delimiter = "_"
    strip_extension: bool = True
    segments: list[SegmentDefinition]

    @field_validator("filenames")
    @classmethod
    def _at_least_one_filename(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one filename is required")
        return v

    @field_validator("segments")
    @classmethod
    def _at_least_one_segment(cls, v: list[SegmentDefinition]) -> list[SegmentDefinition]:
        if not v:
            raise ValueError("at least one segment is required")
        return v


class NamingProfileTestResult(BaseModel):
    """Parser output for a single filename."""

    filename: str
    parsed: dict[str, str]
    unrecognized: list[str]
    warnings: list[str]
