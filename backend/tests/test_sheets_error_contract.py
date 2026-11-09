"""Safe error contract for the Google Sheets read routes (plan 12.5, finding L1).

Header preview, sample preview, and sample confirm all read a spreadsheet through the reader
service account. Google and google-auth failures must come back as controlled JSON errors with
actionable text, classified from the error's structured status/reason (never its display text),
and without raw upstream content in the response or the logs.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from google.auth import exceptions as google_auth_exceptions
from googleapiclient.errors import HttpError
from sqlalchemy import func, select, text

from app.models.sample import Sample

pytestmark = pytest.mark.asyncio

READER_EMAIL = "bioaf-reader-abc1@proj.iam.gserviceaccount.com"
SHEET_URL = "https://docs.google.com/spreadsheets/d/abc123/edit"

# Synthetic markers that must never reach a response or a log record. None of them is registered
# with RedactSecretsFilter (which only knows the SMTP password), so only source-side exclusion
# can keep them out.
SECRET_MARKERS = (
    "SYNTH-SECRET-PROVIDER-BODY",
    "SYNTH-SECRET-PRIVATE-KEY",
    "SYNTH-SECRET-URL-TOKEN",
    "SYNTH-SECRET-CELL",
)
SECRET_URI = "https://sheets.googleapis.com/v4/spreadsheets/abc123?alt=json&access_token=SYNTH-SECRET-URL-TOKEN"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _seed_reader_sa(session, *, key: str | None = None):
    sa_key = (
        key
        if key is not None
        else json.dumps(
            {
                "type": "service_account",
                "project_id": "my-project",
                "private_key_id": "key123",
                "private_key": "-----BEGIN RSA PRIVATE KEY-----\nSYNTH-SECRET-PRIVATE-KEY\n-----END RSA PRIVATE KEY-----\n",
                "client_email": READER_EMAIL,
                "client_id": "123456789",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )
    await session.execute(
        text("""
        INSERT INTO platform_config (key, value) VALUES
            ('sheets_reader_sa_email', :email),
            ('sheets_reader_sa_key', :key),
            ('sheets_reader_sa_created', 'true'),
            ('gcp_credential_source', 'service_account_key')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """).bindparams(email=READER_EMAIL, key=sa_key)
    )
    await session.commit()


def _google_error_body(code: int, status: str, *, legacy_reason: str | None = None, info_reason: str | None = None):
    error: dict = {"code": code, "message": "SYNTH-SECRET-PROVIDER-BODY upstream says no", "status": status}
    if legacy_reason:
        error["errors"] = [{"message": "SYNTH-SECRET-PROVIDER-BODY", "domain": "global", "reason": legacy_reason}]
    if info_reason:
        error["details"] = [
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": info_reason,
                "domain": "googleapis.com",
                "metadata": {"service": "sheets.googleapis.com", "consumer": "SYNTH-SECRET-PROVIDER-BODY"},
            }
        ]
    return {"error": error}


def _http_error(status: int, body) -> HttpError:
    resp = httplib2.Response({"status": status})
    resp.reason = "SYNTH-SECRET-PROVIDER-BODY reason"
    content = json.dumps(body).encode() if isinstance(body, dict) else body
    return HttpError(resp, content, uri=SECRET_URI)


def _sheet_service(*, metadata_error: BaseException | None = None, values_error: BaseException | None = None):
    service = MagicMock()
    if metadata_error is not None:
        service.spreadsheets().get().execute.side_effect = metadata_error
    else:
        service.spreadsheets().get().execute.return_value = {"sheets": [{"properties": {"sheetId": 0, "title": "S1"}}]}
    if values_error is not None:
        service.spreadsheets().values().get().execute.side_effect = values_error
    else:
        service.spreadsheets().values().get().execute.return_value = {
            "values": [["organism", "tissue_type"], ["human", "SYNTH-SECRET-CELL"]]
        }
    return service


async def _experiment(client, admin_token) -> int:
    resp = await client.post("/api/experiments", json={"name": "Sheet errors"}, headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _call(client, admin_token, route: str):
    """POST the given read route ("header" | "preview" | "confirm") for SHEET_URL."""
    if route == "header":
        return await client.post("/api/v1/sheets/preview", json={"sheet_url": SHEET_URL}, headers=_auth(admin_token))
    exp_id = await _experiment(client, admin_token)
    if route == "preview":
        return await client.post(
            f"/api/v1/sheets/{exp_id}/samples/preview", json={"sheet_url": SHEET_URL}, headers=_auth(admin_token)
        )
    return await client.post(
        f"/api/v1/sheets/{exp_id}/samples/confirm",
        json={"sheet_url": SHEET_URL, "column_mappings": {}},
        headers=_auth(admin_token),
    )


def _assert_safe(response):
    body_text = response.text
    for marker in SECRET_MARKERS:
        assert marker not in body_text
    assert "HttpError" not in body_text
    assert "googleapis.com/v4" not in body_text
    detail = response.json()["detail"]
    assert isinstance(detail, str) and detail


ROUTES = ["header", "preview", "confirm"]

# (label, metadata_error, values_error, expected status, expected detail fragment)
UPSTREAM_CASES = [
    (
        "rejected selection",
        _http_error(400, _google_error_body(400, "INVALID_ARGUMENT", legacy_reason="badRequest")),
        None,
        400,
        "Check the spreadsheet URL",
    ),
    (
        "not shared",
        _http_error(403, _google_error_body(403, "PERMISSION_DENIED", legacy_reason="forbidden")),
        None,
        403,
        f"Share it with {READER_EMAIL}",
    ),
    (
        "generic permission denied is access, not API disabled",
        _http_error(403, _google_error_body(403, "PERMISSION_DENIED")),
        None,
        403,
        f"Share it with {READER_EMAIL}",
    ),
    (
        "API disabled (legacy reason)",
        _http_error(403, _google_error_body(403, "PERMISSION_DENIED", legacy_reason="accessNotConfigured")),
        None,
        403,
        "Google Sheets API is not enabled",
    ),
    (
        "API disabled (ErrorInfo reason)",
        _http_error(403, _google_error_body(403, "PERMISSION_DENIED", info_reason="SERVICE_DISABLED")),
        None,
        403,
        "Google Sheets API is not enabled",
    ),
    (
        "missing spreadsheet",
        _http_error(404, _google_error_body(404, "NOT_FOUND", legacy_reason="notFound")),
        None,
        404,
        "Spreadsheet not found",
    ),
    (
        "malformed 403 body",
        _http_error(403, b"<html>SYNTH-SECRET-PROVIDER-BODY</html>"),
        None,
        403,
        f"Share it with {READER_EMAIL}",
    ),
    ("empty 400 body", _http_error(400, b""), None, 400, "Check the spreadsheet URL"),
    (
        "upstream authentication",
        _http_error(401, _google_error_body(401, "UNAUTHENTICATED")),
        None,
        502,
        "reader credentials",
    ),
    (
        "rate limited (429)",
        _http_error(429, _google_error_body(429, "RESOURCE_EXHAUSTED", info_reason="RATE_LIMIT_EXCEEDED")),
        None,
        429,
        "try again",
    ),
    (
        "rate limited (legacy 403 reason)",
        _http_error(403, _google_error_body(403, "PERMISSION_DENIED", legacy_reason="rateLimitExceeded")),
        None,
        429,
        "try again",
    ),
    ("server failure", _http_error(503, _google_error_body(503, "UNAVAILABLE")), None, 502, "temporarily unavailable"),
    ("timeout", TimeoutError("SYNTH-SECRET-URL-TOKEN timed out"), None, 504, "did not respond in time"),
    (
        "transport failure",
        httplib2.ServerNotFoundError("Unable to find SYNTH-SECRET-URL-TOKEN"),
        None,
        502,
        "temporarily unavailable",
    ),
    (
        "lazy credential refresh failure",
        google_auth_exceptions.RefreshError("SYNTH-SECRET-PROVIDER-BODY", {"error": "SYNTH-SECRET-PROVIDER-BODY"}),
        None,
        502,
        "reader credentials",
    ),
    (
        "auth transport failure",
        google_auth_exceptions.TransportError("SYNTH-SECRET-URL-TOKEN"),
        None,
        502,
        "temporarily unavailable",
    ),
    (
        "unexpected rejection of bioAF's own values request",
        None,
        _http_error(400, _google_error_body(400, "INVALID_ARGUMENT", legacy_reason="badRequest")),
        500,
        "Something went wrong",
    ),
    (
        "unexpected application failure",
        RuntimeError("SYNTH-SECRET-PROVIDER-BODY boom"),
        None,
        500,
        "Something went wrong",
    ),
]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "label,metadata_error,values_error,expected_status,expected_fragment",
    UPSTREAM_CASES,
    ids=[c[0] for c in UPSTREAM_CASES],
)
async def test_upstream_failures_are_classified_and_safe(
    client,
    admin_token,
    session,
    route,
    label,
    metadata_error,
    values_error,
    expected_status,
    expected_fragment,
):
    await _seed_reader_sa(session)
    with (
        patch(
            "app.services.google_sheets_service.discovery_build",
            return_value=_sheet_service(metadata_error=metadata_error, values_error=values_error),
        ),
        patch("app.services.sheets_reader_sa_service.get_credentials_provider", return_value=MagicMock()),
    ):
        response = await _call(client, admin_token, route)

    assert response.status_code == expected_status, response.text
    _assert_safe(response)
    assert expected_fragment.lower() in response.json()["detail"].lower()


@pytest.mark.parametrize("route", ROUTES)
async def test_malformed_stored_reader_key_is_a_safe_configuration_error(client, admin_token, session, route):
    """A stored reader key that is not valid JSON fails at credential construction. It is an upstream
    configuration problem (502), not invalid spreadsheet input and not a bioAF login failure."""
    await _seed_reader_sa(session, key='{"private_key": "SYNTH-SECRET-PRIVATE-KEY", not json')
    with patch("app.services.google_sheets_service.discovery_build") as build:
        response = await _call(client, admin_token, route)

    assert response.status_code == 502, response.text
    _assert_safe(response)
    assert "reader credentials" in response.json()["detail"]
    build.assert_not_called()


@pytest.mark.parametrize("route", ROUTES)
async def test_missing_default_credentials_is_a_safe_configuration_error(client, admin_token, session, route):
    await _seed_reader_sa(session)
    provider = MagicMock()
    provider.load_credentials.side_effect = google_auth_exceptions.DefaultCredentialsError(
        "SYNTH-SECRET-PROVIDER-BODY could not find ADC"
    )
    with (
        patch("app.services.google_sheets_service.discovery_build") as build,
        patch("app.services.sheets_reader_sa_service.get_credentials_provider", return_value=provider),
    ):
        response = await _call(client, admin_token, route)

    assert response.status_code == 502, response.text
    _assert_safe(response)
    assert "reader credentials" in response.json()["detail"]
    build.assert_not_called()


@pytest.mark.parametrize("route", ROUTES)
async def test_reader_not_configured_keeps_setup_guidance(client, admin_token, session, route):
    response = await _call(client, admin_token, route)
    assert response.status_code == 400
    assert "Settings > Integrations > GCP" in response.json()["detail"]


async def test_failed_confirm_creates_no_samples(client, admin_token, session):
    await _seed_reader_sa(session)
    with (
        patch(
            "app.services.google_sheets_service.discovery_build",
            return_value=_sheet_service(metadata_error=_http_error(503, _google_error_body(503, "UNAVAILABLE"))),
        ),
        patch("app.services.sheets_reader_sa_service.get_credentials_provider", return_value=MagicMock()),
    ):
        response = await _call(client, admin_token, "confirm")
    assert response.status_code == 502
    assert (await session.execute(select(func.count()).select_from(Sample))).scalar_one() == 0


@pytest.mark.parametrize(
    "error",
    [
        _http_error(403, _google_error_body(403, "PERMISSION_DENIED", info_reason="SERVICE_DISABLED")),
        _http_error(503, _google_error_body(503, "UNAVAILABLE")),
        google_auth_exceptions.RefreshError("SYNTH-SECRET-PROVIDER-BODY"),
        RuntimeError("SYNTH-SECRET-PROVIDER-BODY boom"),
    ],
    ids=["api-disabled", "server", "refresh", "unexpected"],
)
async def test_diagnostics_are_useful_and_contain_no_raw_upstream_content(client, admin_token, session, caplog, error):
    """caplog's handler carries no RedactSecretsFilter, so this proves exclusion at the source."""
    await _seed_reader_sa(session)
    caplog.set_level(logging.DEBUG)
    with (
        patch("app.services.google_sheets_service.discovery_build", return_value=_sheet_service(metadata_error=error)),
        patch("app.services.sheets_reader_sa_service.get_credentials_provider", return_value=MagicMock()),
    ):
        await _call(client, admin_token, "header")

    sheets_records = [r for r in caplog.records if r.name.startswith("bioaf.sheets")]
    assert sheets_records, "a diagnostic record is emitted for the failed read"
    formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")
    for record in caplog.records:
        rendered = formatter.format(record) + repr(record.__dict__)
        for marker in SECRET_MARKERS:
            assert marker not in rendered
        if record.name.startswith("bioaf.sheets"):
            assert record.exc_info is None
    joined = " ".join(r.getMessage() for r in sheets_records)
    assert "operation=" in joined and "category=" in joined
