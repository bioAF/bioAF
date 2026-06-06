"""Schemas for the unified document/file search (LK-SPEC-D, F-LKD-04)."""

from datetime import datetime

from pydantic import BaseModel


class DataSearchItem(BaseModel):
    """A normalized hit spanning Data & Files files and Lab Knowledge documents."""

    kind: str  # "file" | "lab_document"
    id: int
    name: str
    file_type: str | None = None
    size_bytes: int | None = None
    updated_at: datetime
    href: str
    experiment_id: int | None = None
    source: str | None = None


class DataSearchResponse(BaseModel):
    items: list[DataSearchItem]
