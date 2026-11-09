"""Google Sheets import API endpoints.

Covers both experiment-level field import (column headers only) and
sample-level data import (full sheet contents piped through the
existing CSV parsing pipeline).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
from app.api.sample_imports import run_sample_import
from app.api.scoped_loaders import require_owned_experiment
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
from app.services.sample_import_service import experiment_column_aliases
from app.services.csv_service import COLUMN_MAP, SAMPLE_FIELDS, _normalize_header, preview_sample_csv
from app.exceptions import ValidationError
from app.services import google_sheets_errors as sheets_errors
from app.services.google_sheets_service import parse_sheet_url, read_all_rows_as_csv, read_header_row

router = APIRouter(prefix="/api/v1/sheets", tags=["sheets_import"])

_API_DISABLED_DETAIL = (
    "The Google Sheets API is not enabled in your GCP project. "
    "Enable it at: https://console.cloud.google.com/apis/library/sheets.googleapis.com"
)

# Fixed user-facing text per failure category. Access denial is built per request because it names
# the reader account the user must share the sheet with.
_SHEETS_ERRORS: dict[str, tuple[int, str]] = {
    sheets_errors.INVALID_SELECTION: (
        400,
        "Google Sheets could not open that spreadsheet or tab. Check the spreadsheet URL, including the tab, "
        "and try again.",
    ),
    sheets_errors.API_DISABLED: (403, _API_DISABLED_DETAIL),
    sheets_errors.NOT_FOUND: (404, "Spreadsheet not found. Check the URL and try again."),
    sheets_errors.UPSTREAM_AUTH: (
        502,
        "Google did not accept bioAF's reader credentials. An administrator should check the Google Sheets "
        "reader setup in Settings > Integrations > GCP.",
    ),
    sheets_errors.RATE_LIMITED: (429, "Google Sheets is receiving too many requests. Wait a minute and try again."),
    sheets_errors.TIMEOUT: (504, "Google Sheets did not respond in time. Try again."),
    sheets_errors.UNAVAILABLE: (502, "Google Sheets is temporarily unavailable. Try again in a few minutes."),
    sheets_errors.UNEXPECTED: (
        500,
        "Something went wrong reading the spreadsheet. Try again, and contact your administrator if it keeps "
        "happening.",
    ),
}

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
    (headers, sheet_name), spreadsheet_id = await _read_sheet(
        session, body.sheet_url, read_header_row, operation="header_preview"
    )

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


async def _sheets_http_error(session: AsyncSession, failure: sheets_errors.SheetsFailure) -> HTTPException:
    if failure.category == sheets_errors.ACCESS_DENIED:
        status = await sheets_reader_sa_service.get_reader_sa_status(session)
        sa_email = status.get("email") or "the reader service account"
        return HTTPException(
            status_code=403, detail=f"Cannot access this spreadsheet. Share it with {sa_email} and try again."
        )
    code, detail = _SHEETS_ERRORS.get(failure.category, _SHEETS_ERRORS[sheets_errors.UNEXPECTED])
    return HTTPException(status_code=code, detail=detail)


async def _read_sheet(session: AsyncSession, sheet_url: str, reader, *, operation: str):
    """Shared read path for header and sample reads: load reader credentials, parse the URL, run
    ``reader(creds, spreadsheet_id, gid)``, and translate every failure into a controlled error.

    Returns ``(reader_result, spreadsheet_id)``."""
    try:
        creds = await sheets_reader_sa_service.get_reader_credentials(session)
        spreadsheet_id, gid = parse_sheet_url(sheet_url)
        return reader(creds, spreadsheet_id, gid), spreadsheet_id
    except (sheets_reader_sa_service.ReaderNotConfiguredError, ValidationError) as exc:
        # Fixed setup guidance or bioAF's own validation text about the sheet's shape.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sheets_errors.SheetsReadError as exc:
        sheets_errors.log_failure(operation, exc.failure)
        raise await _sheets_http_error(session, exc.failure) from exc
    except Exception as exc:
        failure = sheets_errors.SheetsFailure(
            sheets_errors.UNEXPECTED, "application", exception_type=type(exc).__name__
        )
        sheets_errors.log_failure(operation, failure)
        raise await _sheets_http_error(session, failure) from exc


async def _read_sheet_as_csv(
    body: SheetPreviewRequest,
    session: AsyncSession,
) -> tuple[bytes, str]:
    """Shared helper: load reader SA creds, read entire sheet as CSV bytes."""
    result, _ = await _read_sheet(session, body.sheet_url, read_all_rows_as_csv, operation="sample_read")
    return result


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
    await require_owned_experiment(session, experiment_id, int(current_user["org_id"]))
    aliases = await experiment_column_aliases(session, experiment_id, int(current_user["org_id"]))
    csv_bytes, _ = await _read_sheet_as_csv(body, session)
    return preview_sample_csv(csv_bytes, aliases=aliases)


@router.post("/{experiment_id}/samples/confirm")
async def confirm_sheet_samples(
    experiment_id: int,
    body: SheetSampleConfirmRequest,
    current_user: dict = require_permission("experiments", "upload"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Read a Google Sheet, parse it into samples, and create them.

    Uses the same import workflow as the CSV confirm endpoint.
    """
    await require_owned_experiment(session, experiment_id, int(current_user["org_id"]))
    csv_bytes, _ = await _read_sheet_as_csv(SheetPreviewRequest(sheet_url=body.sheet_url), session)
    result = await run_sample_import(session, current_user, experiment_id, csv_bytes, body.column_mappings)
    return {
        "created_count": result.created_count,
        "error_count": len(result.errors),
        "errors": result.errors,
        "custom_fields_created": result.custom_fields_created,
    }
