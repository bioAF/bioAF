"""BillingProvider BAL seam (Phase 9D).

The billing provider is a platform-service provider (like secrets/iam/messaging),
selected by a config-keyed factory. It drains the ``google.cloud.bigquery``
import out of ``services/billing_export_service.py``: the BQ table-prefix
discovery and the month-to-date cost SQL live behind the provider, which returns
raw rows. The bioAF component taxonomy mapping stays in the service.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.billing import (
    DEFAULT_BILLING_BACKEND,
    VALID_BILLING_BACKENDS,
    create_billing_provider,
)
from app.adapters.billing.base import BillingProvider


def test_factory_returns_gcp_provider_by_default():
    provider = create_billing_provider()
    assert isinstance(provider, BillingProvider)
    assert DEFAULT_BILLING_BACKEND == "gcp"
    assert "gcp" in VALID_BILLING_BACKENDS


def test_factory_rejects_unknown_backend():
    with pytest.raises(ValueError, match="Unknown billing backend"):
        create_billing_provider(backend="aws")


def test_factory_passes_credentials_through():
    creds = MagicMock()
    with patch("app.adapters.billing.gcp.GcpBillingProvider") as ctor:
        create_billing_provider(credentials=creds)
        ctor.assert_called_once_with(credentials=creds)


@pytest.mark.asyncio
async def test_verify_dataset_finds_v1_prefix_table():
    from app.adapters.billing.gcp import GcpBillingProvider

    table = MagicMock()
    table.table_id = "gcp_billing_export_v1_ABC123"
    client = MagicMock()
    client.list_tables.return_value = [table]

    with patch("app.adapters.billing.gcp.bigquery.Client", return_value=client):
        result = await GcpBillingProvider(credentials=None).verify_dataset("proj", "ds")

    assert result == {"found": True, "table_id": "gcp_billing_export_v1_ABC123"}
    client.list_tables.assert_called_once_with("proj.ds")


@pytest.mark.asyncio
async def test_verify_dataset_matches_resource_prefix_table():
    from app.adapters.billing.gcp import GcpBillingProvider

    table = MagicMock()
    table.table_id = "gcp_billing_export_resource_v1_DEADBEEF"
    client = MagicMock()
    client.list_tables.return_value = [table]

    with patch("app.adapters.billing.gcp.bigquery.Client", return_value=client):
        result = await GcpBillingProvider(credentials=None).verify_dataset("proj", "ds")

    assert result == {"found": True, "table_id": "gcp_billing_export_resource_v1_DEADBEEF"}


@pytest.mark.asyncio
async def test_verify_dataset_returns_not_found_when_no_export_table():
    from app.adapters.billing.gcp import GcpBillingProvider

    other = MagicMock()
    other.table_id = "some_other_table"
    client = MagicMock()
    client.list_tables.return_value = [other]

    with patch("app.adapters.billing.gcp.bigquery.Client", return_value=client):
        result = await GcpBillingProvider(credentials=None).verify_dataset("proj", "ds")

    assert result == {"found": False}


@pytest.mark.asyncio
async def test_verify_dataset_passes_credentials_to_client():
    from app.adapters.billing.gcp import GcpBillingProvider

    creds = MagicMock()
    client = MagicMock()
    client.list_tables.return_value = []

    with patch("app.adapters.billing.gcp.bigquery.Client", return_value=client) as ctor:
        await GcpBillingProvider(credentials=creds).verify_dataset("proj", "ds")
        ctor.assert_called_once_with(project="proj", credentials=creds)


@pytest.mark.asyncio
async def test_query_mtd_costs_returns_raw_rows():
    """The provider returns raw {service_name, net_cost, usage_date}; component
    mapping is the service's job (bioAF taxonomy)."""
    from app.adapters.billing.gcp import GcpBillingProvider

    rows = [
        MagicMock(service_name="Compute Engine", net_cost=150.50, usage_date=date(2026, 3, 15)),
        MagicMock(service_name="Cloud Storage", net_cost=25.10, usage_date=date(2026, 3, 15)),
    ]
    job = MagicMock()
    job.result.return_value = rows
    client = MagicMock()
    client.query.return_value = job

    with patch("app.adapters.billing.gcp.bigquery.Client", return_value=client):
        result = await GcpBillingProvider(credentials=None).query_mtd_costs(
            "proj", "ds", "gcp_billing_export_v1_ABC123"
        )

    assert result == [
        {"service_name": "Compute Engine", "net_cost": 150.50, "usage_date": date(2026, 3, 15)},
        {"service_name": "Cloud Storage", "net_cost": 25.10, "usage_date": date(2026, 3, 15)},
    ]


@pytest.mark.asyncio
async def test_query_mtd_costs_passes_credentials_to_client():
    from app.adapters.billing.gcp import GcpBillingProvider

    creds = MagicMock()
    job = MagicMock()
    job.result.return_value = []
    client = MagicMock()
    client.query.return_value = job

    with patch("app.adapters.billing.gcp.bigquery.Client", return_value=client) as ctor:
        await GcpBillingProvider(credentials=creds).query_mtd_costs("proj", "ds", "tbl")
        ctor.assert_called_once_with(project="proj", credentials=creds)
