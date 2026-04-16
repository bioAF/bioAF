"""Tests for Google Sheets import API endpoints.

Reader SA management and sheet preview endpoints.
All GCP / Sheets API calls are mocked.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_reader_sa(session, email: str = "bioaf-reader-abc1@proj.iam.gserviceaccount.com"):
    """Insert reader SA config into platform_config."""
    sa_key = json.dumps(
        {
            "type": "service_account",
            "project_id": "my-project",
            "private_key_id": "key123",
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n",
            "client_email": email,
            "client_id": "123456789",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    await session.execute(
        text("""
        INSERT INTO platform_config (key, value) VALUES
            ('sheets_reader_sa_email', :email),
            ('sheets_reader_sa_key', :key),
            ('sheets_reader_sa_created', 'true')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """).bindparams(email=email, key=sa_key)
    )
    await session.commit()


async def _seed_gcp_config(session):
    """Insert minimal GCP config for SA creation tests."""
    sa_key = json.dumps(
        {
            "type": "service_account",
            "project_id": "my-project",
            "private_key_id": "key123",
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n",
            "client_email": "bioaf@my-project.iam.gserviceaccount.com",
            "client_id": "123456789",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    await session.execute(
        text("""
        INSERT INTO platform_config (key, value) VALUES
            ('gcp_project_id', 'my-project'),
            ('gcp_credential_source', 'service_account_key'),
            ('gcp_service_account_key', :key),
            ('gcp_service_account_email', '')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """).bindparams(key=sa_key)
    )
    await session.commit()


# ---------------------------------------------------------------------------
# GET /reader-sa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_reader_sa_not_configured(client, admin_token, session):
    """GET /api/v1/sheets/reader-sa returns exists=false when not configured."""
    response = await client.get(
        "/api/v1/sheets/reader-sa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is False
    assert data["email"] is None


@pytest.mark.asyncio
async def test_get_reader_sa_configured(client, admin_token, session):
    """GET /api/v1/sheets/reader-sa returns the SA email when configured."""
    await _seed_reader_sa(session)

    response = await client.get(
        "/api/v1/sheets/reader-sa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is True
    assert "bioaf-reader" in data["email"]


@pytest.mark.asyncio
async def test_get_reader_sa_requires_auth(client, session):
    """GET /api/v1/sheets/reader-sa requires authentication."""
    response = await client.get("/api/v1/sheets/reader-sa")
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /reader-sa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_reader_sa_success(client, admin_token, session):
    """POST /api/v1/sheets/reader-sa creates the SA and returns email."""
    await _seed_gcp_config(session)

    mock_iam = MagicMock()
    mock_iam.projects().serviceAccounts().create().execute.return_value = {
        "email": "bioaf-reader-1234@my-project.iam.gserviceaccount.com"
    }
    mock_iam.projects().serviceAccounts().keys().create().execute.return_value = {
        "privateKeyData": "eyJ0eXBlIjoic2VydmljZV9hY2NvdW50In0="  # base64 of {"type":"service_account"}
    }

    mock_usage = MagicMock()
    mock_usage.services().enable().execute.return_value = {}

    def fake_build(api, version, **kwargs):
        if api == "iam":
            return mock_iam
        if api == "serviceusage":
            return mock_usage
        return MagicMock()

    with (
        patch("app.services.sheets_reader_sa_service.discovery_build", side_effect=fake_build),
        patch(
            "app.services.sheets_reader_sa_service.service_account.Credentials.from_service_account_info",
            return_value=MagicMock(),
        ),
    ):
        response = await client.post(
            "/api/v1/sheets/reader-sa",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "bioaf-reader" in data["email"]
    assert data["message"] == "Reader service account created successfully"


@pytest.mark.asyncio
async def test_create_reader_sa_idempotent(client, admin_token, session):
    """POST /api/v1/sheets/reader-sa returns existing SA when already created."""
    await _seed_reader_sa(session, "bioaf-reader-existing@proj.iam.gserviceaccount.com")

    response = await client.post(
        "/api/v1/sheets/reader-sa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "bioaf-reader-existing@proj.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_create_reader_sa_no_gcp_config(client, admin_token, session):
    """POST /api/v1/sheets/reader-sa returns 400 when GCP is not configured."""
    response = await client.post(
        "/api/v1/sheets/reader-sa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_reader_sa_viewer_forbidden(client, viewer_token, session):
    """Viewers cannot create the reader SA."""
    response = await client.post(
        "/api/v1/sheets/reader-sa",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /reader-sa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_reader_sa_success(client, admin_token, session):
    """DELETE /api/v1/sheets/reader-sa removes the SA config."""
    await _seed_reader_sa(session)
    await _seed_gcp_config(session)

    with patch("app.services.sheets_reader_sa_service.discovery_build") as mock_build:
        mock_iam = MagicMock()
        mock_iam.projects().serviceAccounts().delete().execute.return_value = {}
        mock_build.return_value = mock_iam

        response = await client.delete(
            "/api/v1/sheets/reader-sa",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200

    # Verify SA config was removed
    get_response = await client.get(
        "/api/v1/sheets/reader-sa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert get_response.json()["exists"] is False


@pytest.mark.asyncio
async def test_delete_reader_sa_noop_when_not_configured(client, admin_token, session):
    """DELETE /api/v1/sheets/reader-sa succeeds even when no SA exists."""
    response = await client.delete(
        "/api/v1/sheets/reader-sa",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST /preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_sheet_headers(client, admin_token, session):
    """POST /api/v1/sheets/preview returns classified columns."""
    await _seed_reader_sa(session)

    with (
        patch("app.services.google_sheets_service.discovery_build") as mock_build,
        patch(
            "app.services.sheets_reader_sa_service.service_account.Credentials.from_service_account_info",
            return_value=MagicMock(),
        ),
    ):
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.return_value = {
            "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]
        }
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [["organism", "tissue_type", "centrifuge_rpm", "barcode"]]
        }
        mock_build.return_value = mock_service

        response = await client.post(
            "/api/v1/sheets/preview",
            json={"sheet_url": "https://docs.google.com/spreadsheets/d/abc123/edit"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["spreadsheet_id"] == "abc123"
    assert data["sheet_name"] == "Sheet1"
    assert len(data["columns"]) == 4

    # organism and tissue_type should be recognized and defaultable
    recognized = {c["mapped_to"]: c for c in data["recognized_columns"]}
    assert "organism" in recognized
    assert recognized["organism"]["defaultable"] is True
    assert "tissue_type" in recognized
    assert recognized["tissue_type"]["defaultable"] is True

    # centrifuge_rpm and barcode should be unknown
    assert "centrifuge_rpm" in data["unknown_columns"]
    assert "barcode" in data["unknown_columns"]


@pytest.mark.asyncio
async def test_preview_sheet_with_aliases(client, admin_token, session):
    """Column aliases from COLUMN_MAP are resolved correctly."""
    await _seed_reader_sa(session)

    with (
        patch("app.services.google_sheets_service.discovery_build") as mock_build,
        patch(
            "app.services.sheets_reader_sa_service.service_account.Credentials.from_service_account_info",
            return_value=MagicMock(),
        ),
    ):
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.return_value = {
            "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]
        }
        # "tissue" is an alias for "tissue_type", "treatment" for "treatment_condition"
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [["tissue", "treatment", "custom_field_1"]]
        }
        mock_build.return_value = mock_service

        response = await client.post(
            "/api/v1/sheets/preview",
            json={"sheet_url": "https://docs.google.com/spreadsheets/d/xyz789/edit"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    recognized_map = {c["header"]: c["mapped_to"] for c in data["recognized_columns"]}
    assert recognized_map["tissue"] == "tissue_type"
    assert recognized_map["treatment"] == "treatment_condition"
    assert "custom_field_1" in data["unknown_columns"]


@pytest.mark.asyncio
async def test_preview_sheet_no_reader_sa(client, admin_token, session):
    """POST /api/v1/sheets/preview returns 400 when reader SA not configured."""
    response = await client.post(
        "/api/v1/sheets/preview",
        json={"sheet_url": "https://docs.google.com/spreadsheets/d/abc123/edit"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert "not configured" in response.json()["detail"]


@pytest.mark.asyncio
async def test_preview_sheet_invalid_url(client, admin_token, session):
    """POST /api/v1/sheets/preview returns 422 for non-Sheets URL."""
    response = await client.post(
        "/api/v1/sheets/preview",
        json={"sheet_url": "https://example.com/not-a-sheet"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_preview_sheet_not_shared(client, admin_token, session):
    """POST /api/v1/sheets/preview returns 403 when sheet not shared with SA."""
    await _seed_reader_sa(session)

    with (
        patch("app.services.google_sheets_service.discovery_build") as mock_build,
        patch(
            "app.services.sheets_reader_sa_service.service_account.Credentials.from_service_account_info",
            return_value=MagicMock(),
        ),
    ):
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.side_effect = Exception(
            "HttpError 403: The caller does not have permission"
        )
        mock_build.return_value = mock_service

        response = await client.post(
            "/api/v1/sheets/preview",
            json={"sheet_url": "https://docs.google.com/spreadsheets/d/abc123/edit"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 403
    assert "Share it with" in response.json()["detail"]


@pytest.mark.asyncio
async def test_preview_non_defaultable_fields_recognized_with_flag(client, admin_token, session):
    """Fields that match COLUMN_MAP but are not defaultable are recognized with defaultable=false."""
    await _seed_reader_sa(session)

    with (
        patch("app.services.google_sheets_service.discovery_build") as mock_build,
        patch(
            "app.services.sheets_reader_sa_service.service_account.Credentials.from_service_account_info",
            return_value=MagicMock(),
        ),
    ):
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.return_value = {
            "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]
        }
        # qc_status maps via COLUMN_MAP but is NOT in DEFAULTABLE_SAMPLE_FIELDS
        mock_service.spreadsheets().values().get().execute.return_value = {"values": [["organism", "qc_status"]]}
        mock_build.return_value = mock_service

        response = await client.post(
            "/api/v1/sheets/preview",
            json={"sheet_url": "https://docs.google.com/spreadsheets/d/abc123/edit"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    recognized = {c["mapped_to"]: c for c in data["recognized_columns"]}
    assert "organism" in recognized
    assert recognized["organism"]["defaultable"] is True
    # qc_status is recognized but not defaultable
    assert "qc_status" in recognized
    assert recognized["qc_status"]["defaultable"] is False
    assert "qc_status" not in data["unknown_columns"]


# ---------------------------------------------------------------------------
# POST /{experiment_id}/samples/preview (Google Sheet -> sample preview)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sheet_sample_preview(client, admin_token, session):
    """POST /api/v1/sheets/{id}/samples/preview returns CSV-style preview from a sheet."""
    await _seed_reader_sa(session)

    # Create an experiment first
    exp_resp = await client.post(
        "/api/experiments",
        json={"name": "Sheet Sample Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert exp_resp.status_code == 200
    exp_id = exp_resp.json()["id"]

    with (
        patch("app.services.google_sheets_service.discovery_build") as mock_build,
        patch(
            "app.services.sheets_reader_sa_service.service_account.Credentials.from_service_account_info",
            return_value=MagicMock(),
        ),
    ):
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.return_value = {
            "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]
        }
        # Full sheet with header + 2 data rows
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [
                ["organism", "tissue_type", "custom_col"],
                ["human", "PBMC", "val1"],
                ["mouse", "brain", "val2"],
            ]
        }
        mock_build.return_value = mock_service

        response = await client.post(
            f"/api/v1/sheets/{exp_id}/samples/preview",
            json={"sheet_url": "https://docs.google.com/spreadsheets/d/abc123/edit"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 2
    assert len(data["recognized_columns"]) >= 2
    recognized_fields = {c["mapped_to"] for c in data["recognized_columns"]}
    assert "organism" in recognized_fields
    assert "tissue_type" in recognized_fields


@pytest.mark.asyncio
async def test_sheet_sample_preview_no_reader_sa(client, admin_token, session):
    """Sheet sample preview returns 400 when reader SA not configured."""
    response = await client.post(
        "/api/v1/sheets/1/samples/preview",
        json={"sheet_url": "https://docs.google.com/spreadsheets/d/abc123/edit"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert "not configured" in response.json()["detail"]
