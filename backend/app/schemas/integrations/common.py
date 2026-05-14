from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IntegrationError(BaseModel):
    """Standard error envelope for /api/v1/integrations/*."""

    error: str = Field(..., description="Machine-readable error code")
    detail: str = Field(..., description="Human-readable message")
    request_id: str | None = None


class CustomFieldIn(BaseModel):
    field_name: str = Field(..., max_length=255)
    field_value: str | None = None


class CustomFieldOut(BaseModel):
    field_name: str
    field_value: str | None = None


class CursorPage(BaseModel):
    items: list[Any]
    next_cursor: str | None = None
