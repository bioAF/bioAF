"""Schemas for Google Sheets column import."""

from pydantic import BaseModel, field_validator


class SheetPreviewRequest(BaseModel):
    sheet_url: str

    @field_validator("sheet_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if "docs.google.com/spreadsheets" not in v:
            raise ValueError("Must be a Google Sheets URL")
        return v


class RecognizedColumn(BaseModel):
    header: str
    mapped_to: str
    defaultable: bool


class SheetPreviewResponse(BaseModel):
    spreadsheet_id: str
    sheet_name: str
    columns: list[str]
    recognized_columns: list[RecognizedColumn]
    unknown_columns: list[str]


class ReaderSAStatusResponse(BaseModel):
    exists: bool
    email: str | None = None


class ReaderSACreateResponse(BaseModel):
    email: str
    message: str
    warning: str | None = None


class SheetSampleConfirmRequest(BaseModel):
    sheet_url: str
    column_mappings: dict[str, str] = {}

    @field_validator("sheet_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if "docs.google.com/spreadsheets" not in v:
            raise ValueError("Must be a Google Sheets URL")
        return v
