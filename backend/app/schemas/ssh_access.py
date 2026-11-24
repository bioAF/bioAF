"""Schemas for Settings > System Settings > Security and SSH access (plan 03B)."""

from pydantic import BaseModel, Field


class RangeEntry(BaseModel):
    id: str | None = None
    name: str = Field(default="", max_length=80)
    cidr: str = Field(max_length=64)


class WorkNodeDraftRequest(BaseModel):
    enabled: bool
    ranges: list[RangeEntry] = Field(default_factory=list, max_length=50)
    base_version: int


class ApplyRequest(BaseModel):
    draft_version: int
