"""Tests for BigQuery billing export setup and cost sync (ADR-028)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_platform_config(session, overrides: dict | None = None):
    """Seed platform_config keys needed by billing export endpoints."""
    defaults = {
        "gcp_credentials_configured": "true",
        "gcp_project_id": "test-project-123",
        "terraform_initialized": "true",
        "billing_export_configured": "false",
        "billing_export_dataset": "",
        "billing_export_table": "",
    }
    if overrides:
        defaults.update(overrides)
    for key, value in defaults.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_billing_export_status_unconfigured(client: AsyncClient, admin_token: str, session):
    await _seed_platform_config(session)
    response = await client.get(
        "/api/v1/infrastructure/billing-export/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is False
    assert data["dataset_id"] == ""
    assert "console.cloud.google.com" in data["console_url"]


@pytest.mark.asyncio
async def test_billing_export_status_configured(client: AsyncClient, admin_token: str, session):
    await _seed_platform_config(
        session,
        {
            "billing_export_configured": "true",
            "billing_export_dataset": "billing_export",
            "billing_export_table": "gcp_billing_export_v1_ABC123",
        },
    )
    response = await client.get(
        "/api/v1/infrastructure/billing-export/status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["dataset_id"] == "billing_export"


@pytest.mark.asyncio
async def test_billing_export_status_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/v1/infrastructure/billing-export/status",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /enable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_billing_export_enable_requires_terraform(client: AsyncClient, admin_token: str, session):
    await _seed_platform_config(session, {"terraform_initialized": "false"})
    response = await client.post(
        "/api/v1/infrastructure/billing-export/enable",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert "terraform" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_billing_export_enable_success(client: AsyncClient, admin_token: str, session):
    await _seed_platform_config(session)

    mock_result = {"status": "completed"}
    with patch(
        "app.api.billing_export.deploy_billing_export_module",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        response = await client.post(
            "/api/v1/infrastructure/billing-export/enable",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_billing_export_enable_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    response = await client.post(
        "/api/v1/infrastructure/billing-export/enable",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_billing_export_verify_not_found(client: AsyncClient, admin_token: str, session):
    await _seed_platform_config(session, {"billing_export_dataset": "billing_export"})

    with patch(
        "app.api.billing_export.BillingExportService.verify_dataset",
        new_callable=AsyncMock,
        return_value={"found": False},
    ):
        response = await client.post(
            "/api/v1/infrastructure/billing-export/verify",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is False
    assert "24 hours" in data["message"]


@pytest.mark.asyncio
async def test_billing_export_verify_success(client: AsyncClient, admin_token: str, session):
    await _seed_platform_config(session, {"billing_export_dataset": "billing_export"})

    with patch(
        "app.api.billing_export.BillingExportService.verify_dataset",
        new_callable=AsyncMock,
        return_value={"found": True, "table_id": "gcp_billing_export_v1_ABC123"},
    ):
        response = await client.post(
            "/api/v1/infrastructure/billing-export/verify",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["table_id"] == "gcp_billing_export_v1_ABC123"

    # Verify platform_config was updated
    row = (
        await session.execute(text("SELECT value FROM platform_config WHERE key = 'billing_export_configured'"))
    ).fetchone()
    assert row is not None
    assert row[0] == "true"


@pytest.mark.asyncio
async def test_billing_export_verify_no_dataset(client: AsyncClient, admin_token: str, session):
    """Verify fails gracefully when no dataset has been created yet."""
    await _seed_platform_config(session, {"billing_export_dataset": ""})
    response = await client.post(
        "/api/v1/infrastructure/billing-export/verify",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# BillingExportService unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map_service_to_component():
    from app.services.billing_export_service import BillingExportService

    assert BillingExportService.map_service_to_component("Compute Engine") == "compute"
    assert BillingExportService.map_service_to_component("Cloud Storage") == "storage"
    assert BillingExportService.map_service_to_component("Kubernetes Engine") == "node"
    assert BillingExportService.map_service_to_component("Cloud SQL") == "other"
    assert BillingExportService.map_service_to_component("Unknown Service") == "other"


@pytest.mark.asyncio
async def test_query_mtd_costs_returns_mapped_data():
    """Verify BQ results are correctly mapped to component cost dicts."""
    from app.services.billing_export_service import BillingExportService

    mock_rows = [
        MagicMock(service_name="Compute Engine", net_cost=150.50, usage_date=date(2026, 3, 15)),
        MagicMock(service_name="Cloud Storage", net_cost=25.10, usage_date=date(2026, 3, 15)),
        MagicMock(service_name="Kubernetes Engine", net_cost=80.00, usage_date=date(2026, 3, 15)),
    ]
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = mock_rows

    mock_bq_client = MagicMock()
    mock_bq_client.query.return_value = mock_query_job

    with patch("app.services.billing_export_service.bigquery.Client", return_value=mock_bq_client):
        results = await BillingExportService.query_mtd_costs(
            "test-project", "billing_export", "gcp_billing_export_v1_ABC123"
        )

    assert len(results) == 3
    compute_row = next(r for r in results if r["service_name"] == "Compute Engine")
    assert compute_row["component"] == "compute"
    assert compute_row["net_cost"] == 150.50

    storage_row = next(r for r in results if r["service_name"] == "Cloud Storage")
    assert storage_row["component"] == "storage"


@pytest.mark.asyncio
async def test_verify_dataset_passes_credentials_to_bq_client():
    """verify_dataset must pass explicit credentials to the BQ client."""
    from app.services.billing_export_service import BillingExportService

    mock_creds = MagicMock()
    mock_bq_client = MagicMock()
    mock_bq_client.list_tables.return_value = []

    with patch("app.services.billing_export_service.bigquery.Client", return_value=mock_bq_client) as mock_ctor:
        await BillingExportService.verify_dataset("proj", "ds", credentials=mock_creds)
        mock_ctor.assert_called_once_with(project="proj", credentials=mock_creds)


@pytest.mark.asyncio
async def test_query_mtd_costs_passes_credentials_to_bq_client():
    """query_mtd_costs must pass explicit credentials to the BQ client."""
    from app.services.billing_export_service import BillingExportService

    mock_creds = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = []
    mock_bq_client = MagicMock()
    mock_bq_client.query.return_value = mock_query_job

    with patch("app.services.billing_export_service.bigquery.Client", return_value=mock_bq_client) as mock_ctor:
        await BillingExportService.query_mtd_costs("proj", "ds", "table", credentials=mock_creds)
        mock_ctor.assert_called_once_with(project="proj", credentials=mock_creds)


# ---------------------------------------------------------------------------
# CostService BQ integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_billing_data_uses_bq_when_configured(admin_user, session):
    """When billing export is configured, sync should query BQ for historical data."""
    from app.services.cost_service import CostService
    from app.models.cost_record import CostRecord
    from sqlalchemy import select

    org_id = admin_user.organization_id

    # Seed platform_config for BQ
    await _seed_platform_config(
        session,
        {
            "billing_export_configured": "true",
            "billing_export_dataset": "billing_export",
            "billing_export_table": "gcp_billing_export_v1_ABC123",
            "gcp_project_id": "test-project",
        },
    )

    today = date.today()
    yesterday = date(today.year, today.month, max(today.day - 1, 1))

    mock_bq_results = [
        {
            "service_name": "Compute Engine",
            "component": "compute",
            "net_cost": 45.00,
            "usage_date": yesterday,
        },
        {
            "service_name": "Cloud Storage",
            "component": "storage",
            "net_cost": 12.50,
            "usage_date": yesterday,
        },
    ]

    with patch(
        "app.services.billing_export_service.BillingExportService.query_mtd_costs",
        new_callable=AsyncMock,
        return_value=mock_bq_results,
    ):
        await CostService.sync_billing_data(session, org_id)
    await session.flush()

    result = await session.execute(
        select(CostRecord).where(
            CostRecord.organization_id == org_id,
        )
    )
    records = list(result.scalars().all())
    components = {r.component for r in records}

    # BQ data should have populated compute and storage for yesterday
    assert "compute" in components
    assert "storage" in components


@pytest.mark.asyncio
async def test_sync_billing_data_falls_back_to_adapters(admin_user, session):
    """When billing export is not configured, sync should use adapter path."""
    from app.services.cost_service import CostService
    from app.models.cost_record import CostRecord
    from sqlalchemy import select

    org_id = admin_user.organization_id

    await _seed_platform_config(session, {"billing_export_configured": "false"})

    await CostService.sync_billing_data(session, org_id)
    await session.flush()

    today = date.today()
    result = await session.execute(
        select(CostRecord).where(
            CostRecord.organization_id == org_id,
            CostRecord.record_date == today,
        )
    )
    records = list(result.scalars().all())
    components = {r.component for r in records}

    assert "node" in components
    assert "storage" in components
    assert "compute" in components
