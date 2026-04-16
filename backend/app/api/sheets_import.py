"""Google Sheets import API endpoints.

Covers both experiment-level field import (column headers only) and
sample-level data import (full sheet contents piped through the
existing CSV parsing pipeline).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.database import get_session
from app.models.experiment_field_default import DEFAULTABLE_SAMPLE_FIELDS
from app.schemas.sheets_import import (
    ReaderSACreateResponse,
    ReaderSAStatusResponse,
    RecognizedColumn,
    SheetPreviewRequest,
    SheetPreviewResponse,
    SheetSampleConfirmRequest,
)
from app.services import sheets_reader_sa_service
from app.services.csv_service import COLUMN_MAP, SAMPLE_FIELDS, _normalize_header, parse_sample_csv, preview_sample_csv
from app.services.google_sheets_service import parse_sheet_url, read_all_rows_as_csv, read_header_row

router = APIRouter(prefix="/api/v1/sheets", tags=["sheets_import"])

# All user-facing sample fields that column headers can be recognized against.
_ALL_SAMPLE_FIELDS = set(SAMPLE_FIELDS)
# The subset that can be configured as experiment-level field defaults.
_DEFAULTABLE_FIELDS = set(DEFAULTABLE_SAMPLE_FIELDS)


@router.get("/reader-sa", response_model=ReaderSAStatusResponse)
async def get_reader_sa_status(
    current_user: dict = require_permission("infrastructure", "view"),
    session: AsyncSession = Depends(get_session),
) -> ReaderSAStatusResponse:
    """Check whether the reader SA exists."""
    status = await sheets_reader_sa_service.get_reader_sa_status(session)
    return ReaderSAStatusResponse(
        exists=bool(status["exists"]),
        email=str(status["email"]) if status["email"] else None,
    )


@router.post("/reader-sa", response_model=ReaderSACreateResponse)
async def create_reader_sa(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> ReaderSACreateResponse:
    """Create the dedicated reader SA for Google Sheets access."""
    try:
        result = await sheets_reader_sa_service.create_reader_sa(session)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create reader service account: {exc}",
        ) from exc
    return ReaderSACreateResponse(
        email=str(result["email"]),
        message="Reader service account created successfully",
        warning=str(result["warning"]) if result.get("warning") else None,
    )


@router.delete("/reader-sa")
async def delete_reader_sa(
    current_user: dict = require_permission("infrastructure", "configure"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Delete the reader SA and its stored credentials."""
    try:
        await sheets_reader_sa_service.delete_reader_sa(session)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete reader service account: {exc}",
        ) from exc
    return {"message": "Reader service account deleted"}


@router.post("/preview", response_model=SheetPreviewResponse)
async def preview_sheet_headers(
    body: SheetPreviewRequest,
    current_user: dict = require_permission("experiments", "create"),
    session: AsyncSession = Depends(get_session),
) -> SheetPreviewResponse:
    """Read column headers from a Google Sheet and classify them.

    Columns that match known sample fields (via the CSV column map) and
    are part of the defaultable sample fields list are returned as
    ``recognized_columns``.  Everything else lands in ``unknown_columns``.
    """
    # Load reader SA credentials
    try:
        creds = await sheets_reader_sa_service.get_reader_credentials(session)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Parse the sheet URL
    try:
        spreadsheet_id, gid = parse_sheet_url(body.sheet_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Read headers from the sheet
    try:
        headers, sheet_name = read_header_row(creds, spreadsheet_id, gid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "PERMISSION_DENIED" in msg:
            status = await sheets_reader_sa_service.get_reader_sa_status(session)
            sa_email = status.get("email", "the reader service account")
            # Distinguish "API not enabled" from "sheet not shared"
            if "has not been used" in msg or "it is disabled" in msg or "FORBIDDEN" in msg:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "The Google Sheets API is not enabled in your GCP project. "
                        "Enable it at: https://console.cloud.google.com/apis/library/sheets.googleapis.com"
                    ),
                ) from exc
            raise HTTPException(
                status_code=403,
                detail=(f"Cannot access this spreadsheet. Share it with {sa_email} and try again."),
            ) from exc
        if "404" in msg or "not found" in msg.lower():
            raise HTTPException(
                status_code=404,
                detail="Spreadsheet not found. Check the URL and try again.",
            ) from exc
        raise HTTPException(status_code=500, detail=f"Error reading spreadsheet: {msg}") from exc

    # Classify columns using the existing CSV column map
    recognized: list[RecognizedColumn] = []
    unknown: list[str] = []

    for header in headers:
        normalized = _normalize_header(header)
        mapped_field = COLUMN_MAP.get(normalized)
        if mapped_field and mapped_field in _ALL_SAMPLE_FIELDS:
            recognized.append(
                RecognizedColumn(
                    header=header,
                    mapped_to=mapped_field,
                    defaultable=mapped_field in _DEFAULTABLE_FIELDS,
                )
            )
        else:
            unknown.append(header)

    return SheetPreviewResponse(
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        columns=headers,
        recognized_columns=recognized,
        unknown_columns=unknown,
    )


# ---------------------------------------------------------------------------
# Sample-level import (full sheet data -> CSV pipeline)
# ---------------------------------------------------------------------------


async def _read_sheet_as_csv(
    body: SheetPreviewRequest,
    session: AsyncSession,
) -> tuple[bytes, str]:
    """Shared helper: load reader SA creds, read entire sheet as CSV bytes."""
    try:
        creds = await sheets_reader_sa_service.get_reader_credentials(session)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        spreadsheet_id, gid = parse_sheet_url(body.sheet_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        csv_bytes, sheet_name = read_all_rows_as_csv(creds, spreadsheet_id, gid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "PERMISSION_DENIED" in msg:
            status = await sheets_reader_sa_service.get_reader_sa_status(session)
            sa_email = status.get("email", "the reader service account")
            if "has not been used" in msg or "it is disabled" in msg or "FORBIDDEN" in msg:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "The Google Sheets API is not enabled in your GCP project. "
                        "Enable it at: https://console.cloud.google.com/apis/library/sheets.googleapis.com"
                    ),
                ) from exc
            raise HTTPException(
                status_code=403,
                detail=f"Cannot access this spreadsheet. Share it with {sa_email} and try again.",
            ) from exc
        if "404" in msg or "not found" in msg.lower():
            raise HTTPException(
                status_code=404,
                detail="Spreadsheet not found. Check the URL and try again.",
            ) from exc
        raise HTTPException(status_code=500, detail=f"Error reading spreadsheet: {msg}") from exc

    return csv_bytes, sheet_name


@router.post("/{experiment_id}/samples/preview")
async def preview_sheet_samples(
    experiment_id: int,
    body: SheetPreviewRequest,
    current_user: dict = require_permission("experiments", "upload"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Read a Google Sheet and preview its contents as sample data.

    Returns the same format as the CSV preview endpoint so the frontend
    can reuse the same mapping UI.
    """
    csv_bytes, _ = await _read_sheet_as_csv(body, session)
    return preview_sample_csv(csv_bytes)


@router.post("/{experiment_id}/samples/confirm")
async def confirm_sheet_samples(
    experiment_id: int,
    body: SheetSampleConfirmRequest,
    current_user: dict = require_permission("experiments", "upload"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Read a Google Sheet, parse it into samples, and create them.

    Uses the same parsing and creation logic as the CSV confirm endpoint.
    """
    from app.models.sample_custom_field import SampleCustomField
    from app.services.sample_service import SampleService

    user_id = int(current_user["sub"])
    csv_bytes, _ = await _read_sheet_as_csv(SheetPreviewRequest(sheet_url=body.sheet_url), session)

    parsed_samples, parse_errors, custom_field_rows = parse_sample_csv(
        csv_bytes, experiment_id, column_mappings=body.column_mappings or None
    )

    if not parsed_samples and parse_errors:
        raise HTTPException(400, detail={"errors": parse_errors})

    custom_field_names: list[str] = sorted({name for row in custom_field_rows for name in row})

    created = []
    create_errors = []
    for i, sample_data in enumerate(parsed_samples):
        try:
            sample = await SampleService.create_sample(session, experiment_id, user_id, sample_data)
            created.append(sample)

            if i < len(custom_field_rows) and custom_field_rows[i]:
                for field_name, field_value in custom_field_rows[i].items():
                    session.add(
                        SampleCustomField(
                            sample_id=sample.id,
                            field_name=field_name,
                            field_value=str(field_value),
                        )
                    )
        except HTTPException as e:
            create_errors.append(f"Sample {i + 1}: {e.detail}")

    if created:
        await session.commit()

    return {
        "created_count": len(created),
        "error_count": len(parse_errors) + len(create_errors),
        "errors": parse_errors + create_errors,
        "custom_fields_created": custom_field_names,
    }
