"""Classification of Google Sheets read failures (plan 12.5, L1).

Google API and google-auth errors carry raw upstream payloads, request URLs, and sometimes
credential details in their text. Nothing here reads that text: failures are classified from the
HTTP status and the structured reasons in the error body, and only the resulting category and
numeric status leave this module. Routes translate the category into a fixed, actionable message.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httplib2
from google.auth import exceptions as google_auth_exceptions
from googleapiclient.errors import HttpError

logger = logging.getLogger("bioaf.sheets")

# Categories (route translation lives in api/sheets_import.py):
INVALID_SELECTION = "invalid_selection"  # Google rejected the spreadsheet id/tab the user chose
ACCESS_DENIED = "access_denied"  # the reader account cannot open the sheet
API_DISABLED = "api_disabled"  # the Sheets API is not enabled in the GCP project
NOT_FOUND = "not_found"
UPSTREAM_AUTH = "upstream_auth"  # reader credentials are unusable or rejected by Google
RATE_LIMITED = "rate_limited"
TIMEOUT = "timeout"
UNAVAILABLE = "unavailable"  # Google server or network failure
UNEXPECTED = "unexpected"  # bioAF defect or unrecognized failure

# Phases of a read. A 400 on the metadata lookup means the user's spreadsheet id/tab is bad; a 400
# on the values request means bioAF built a request Google rejected, which is our defect.
METADATA = "metadata"
VALUES = "values"
CREDENTIALS = "credentials"

_API_DISABLED_REASONS = {"accessNotConfigured", "SERVICE_DISABLED", "API_DISABLED"}
_RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED", "quotaExceeded"}


@dataclass(frozen=True)
class SheetsFailure:
    category: str
    phase: str
    upstream_status: int | None = None
    exception_type: str | None = None  # class name only, for unexpected failures


class SheetsReadError(Exception):
    """A classified Sheets failure. Carries no upstream text."""

    def __init__(self, failure: SheetsFailure) -> None:
        super().__init__(failure.category)
        self.failure = failure


def _structured_reasons(content: bytes) -> set[str]:
    """Reasons from both the legacy `error.errors[].reason` and the `error.details[].reason`
    (google.rpc.ErrorInfo) shapes. Malformed or absent JSON yields no reasons."""
    try:
        data = json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return set()
    if isinstance(data, list) and data:
        data = data[0]
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return set()
    reasons: set[str] = set()
    for key in ("errors", "details"):
        items = error.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("reason"), str):
                    reasons.add(item["reason"])
    return reasons


def _classify_http_error(exc: HttpError, phase: str) -> SheetsFailure:
    try:
        status = int(exc.resp.status)
    except (AttributeError, TypeError, ValueError):
        return SheetsFailure(UNEXPECTED, phase)
    reasons = _structured_reasons(exc.content)
    if reasons & _API_DISABLED_REASONS:
        category = API_DISABLED
    elif status == 429 or reasons & _RATE_LIMIT_REASONS:
        category = RATE_LIMITED
    elif status == 401:
        category = UPSTREAM_AUTH
    elif status == 403:
        category = ACCESS_DENIED
    elif status == 404:
        category = NOT_FOUND
    elif status == 400 and phase == METADATA:
        category = INVALID_SELECTION
    elif status in (408, 504):
        category = TIMEOUT
    elif status >= 500:
        category = UNAVAILABLE
    else:
        category = UNEXPECTED
    return SheetsFailure(category, phase, status)


def classify(exc: BaseException, phase: str) -> SheetsFailure:
    """Classify a failure raised while reading a sheet (credential refresh happens lazily here)."""
    if isinstance(exc, HttpError):
        return _classify_http_error(exc, phase)
    if isinstance(exc, (TimeoutError, google_auth_exceptions.TimeoutError)):
        return SheetsFailure(TIMEOUT, phase)
    if isinstance(exc, (google_auth_exceptions.RefreshError, google_auth_exceptions.DefaultCredentialsError)):
        return SheetsFailure(UPSTREAM_AUTH, phase)
    if isinstance(exc, (google_auth_exceptions.TransportError, httplib2.HttpLib2Error, ConnectionError)):
        return SheetsFailure(UNAVAILABLE, phase)
    return SheetsFailure(UNEXPECTED, phase, exception_type=type(exc).__name__)


def classify_credential_construction(exc: BaseException) -> SheetsFailure | None:
    """Classify a failure while building reader credentials from stored configuration.

    Only the narrow credential-construction call is wrapped with this, so malformed stored key
    data (ValueError/KeyError/TypeError from JSON or key parsing) is a credential problem there.
    Returns None for anything else so it propagates as an unexpected failure."""
    if isinstance(exc, google_auth_exceptions.GoogleAuthError):
        return classify(exc, CREDENTIALS)
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return SheetsFailure(UPSTREAM_AUTH, CREDENTIALS)
    return None


def log_failure(operation: str, failure: SheetsFailure) -> None:
    """Emit a diagnostic with allowlisted fields only: fixed operation and category names, the
    numeric upstream status, and (for unexpected failures) the exception class name. Never the
    exception text, payload, URL, or traceback."""
    logger.warning(
        "sheets read failed: operation=%s phase=%s category=%s upstream_status=%s exception_type=%s",
        operation,
        failure.phase,
        failure.category,
        failure.upstream_status if failure.upstream_status is not None else "none",
        failure.exception_type or "none",
    )
