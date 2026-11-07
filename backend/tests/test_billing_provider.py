"""BillingProvider BAL seam (Phase 9D).

The billing provider is a platform-service provider (like secrets/iam/messaging),
selected by a config-keyed factory. It drains the ``google.cloud.bigquery``
import out of ``services/billing_export_service.py``: the BQ table-prefix
discovery and the usage-window cost SQL live behind the provider, which returns
raw rows. The bioAF component taxonomy mapping stays in the service.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.exceptions import ValidationError

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
    with pytest.raises(ValidationError, match="Unknown billing backend"):
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
async def test_query_usage_costs_returns_raw_rows():
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
    client.get_table.return_value.time_partitioning.field = None

    with patch("app.adapters.billing.gcp.bigquery.Client", return_value=client):
        result = await GcpBillingProvider(credentials=None).query_usage_costs(
            "proj", "ds", "gcp_billing_export_v1_ABC123", date(2026, 3, 1), date(2026, 3, 16)
        )

    assert result == [
        {"service_name": "Compute Engine", "net_cost": 150.50, "usage_date": date(2026, 3, 15)},
        {"service_name": "Cloud Storage", "net_cost": 25.10, "usage_date": date(2026, 3, 15)},
    ]


@pytest.mark.asyncio
async def test_query_usage_costs_passes_credentials_to_client():
    from app.adapters.billing.gcp import GcpBillingProvider

    creds = MagicMock()
    job = MagicMock()
    job.result.return_value = []
    client = MagicMock()
    client.query.return_value = job
    client.get_table.return_value.time_partitioning.field = None

    with patch("app.adapters.billing.gcp.bigquery.Client", return_value=client) as ctor:
        await GcpBillingProvider(credentials=creds).query_usage_costs(
            "proj", "ds", "tbl", date(2026, 3, 1), date(2026, 3, 16)
        )
        ctor.assert_called_once_with(project="proj", credentials=creds)


# ---------------------------------------------------------------------------
# Usage-window query contract (plan 7b)
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _export(rows=None, partitioning="ingestion_day"):
    from tests.support.billing_export_table import BillingExportTable

    return BillingExportTable(rows, partitioning=partitioning)


async def _query(table, usage_start: date, usage_end: date):
    from app.adapters.billing.gcp import GcpBillingProvider

    with patch("app.adapters.billing.gcp.bigquery.Client", side_effect=table.client):
        return await GcpBillingProvider(credentials=None).query_usage_costs(
            "proj", "ds", "gcp_billing_export_v1_ABC", usage_start, usage_end
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage_start, usage_end",
    [
        # Routine refresh: previous and current UTC month, excluding today.
        (date(2026, 10, 1), date(2026, 11, 6)),
        # An older explicit backfill.
        (date(2026, 3, 15), date(2026, 4, 1)),
    ],
)
async def test_usage_query_prunes_partitions_from_the_window_start(usage_start, usage_end):
    """The submitted query binds a partition lower bound at the window start and independent usage bounds.

    No upper partition bound: an in-window day's charges can be exported long after the window ends.
    """
    table = _export()
    await _query(table, usage_start, usage_end)

    [(sql, params)] = table.queries
    normalized = " ".join(sql.split())
    assert "_PARTITIONTIME >= @partition_start" in normalized
    assert "usage_start_time >= @usage_start" in normalized
    assert "usage_start_time < @usage_end" in normalized
    assert "_PARTITIONTIME <" not in normalized
    assert "invoice" not in normalized.lower()
    assert "CURRENT_DATE" not in normalized
    assert params == {
        "partition_start": datetime(usage_start.year, usage_start.month, usage_start.day, tzinfo=UTC),
        "usage_start": datetime(usage_start.year, usage_start.month, usage_start.day, tzinfo=UTC),
        "usage_end": datetime(usage_end.year, usage_end.month, usage_end.day, tzinfo=UTC),
    }


@pytest.mark.asyncio
async def test_usage_query_sums_every_invoice_month_for_a_usage_day():
    from tests.support.billing_export_table import ExportRow

    table = _export(
        [
            ExportRow(
                "Compute Engine",
                30.0,
                datetime(2026, 10, 31, 10, tzinfo=UTC),
                "202610",
                datetime(2026, 11, 1, 2, tzinfo=UTC),
            ),
            ExportRow(
                "Compute Engine",
                20.0,
                datetime(2026, 10, 31, 22, tzinfo=UTC),
                "202611",
                datetime(2026, 11, 2, 2, tzinfo=UTC),
            ),
            ExportRow(
                "Compute Engine",
                5.0,
                datetime(2026, 10, 31, 23, tzinfo=UTC),
                "202611",
                datetime(2026, 11, 2, 2, tzinfo=UTC),
                credits=[-1.5],
            ),
        ]
    )
    rows = await _query(table, date(2026, 10, 1), date(2026, 11, 6))
    assert rows == [{"service_name": "Compute Engine", "net_cost": 53.5, "usage_date": date(2026, 10, 31)}]


@pytest.mark.asyncio
async def test_usage_query_keeps_late_exports_and_drops_pre_window_usage():
    from tests.support.billing_export_table import ExportRow

    table = _export(
        [
            # In-window usage exported six weeks after the window ends.
            ExportRow(
                "Cloud Storage",
                4.0,
                datetime(2026, 10, 30, 12, tzinfo=UTC),
                "202610",
                datetime(2026, 12, 15, tzinfo=UTC),
            ),
            # Usage from before the window, ingested inside it.
            ExportRow(
                "Cloud Storage", 9.0, datetime(2026, 9, 30, 23, tzinfo=UTC), "202609", datetime(2026, 10, 2, tzinfo=UTC)
            ),
            # Today's usage (the window end) is excluded.
            ExportRow(
                "Cloud Storage",
                7.0,
                datetime(2026, 11, 6, 0, 30, tzinfo=UTC),
                "202611",
                datetime(2026, 11, 6, 3, tzinfo=UTC),
            ),
        ]
    )
    rows = await _query(table, date(2026, 10, 1), date(2026, 11, 6))
    assert rows == [{"service_name": "Cloud Storage", "net_cost": 4.0, "usage_date": date(2026, 10, 30)}]


@pytest.mark.asyncio
@pytest.mark.parametrize("partitioning", ["none", "column"])
async def test_usage_query_refuses_a_table_it_cannot_prune(partitioning):
    """No silent fallback to an unrestricted full-table scan."""
    from app.adapters.billing.base import UnsupportedBillingExport

    table = _export(partitioning=partitioning)
    with pytest.raises(UnsupportedBillingExport):
        await _query(table, date(2026, 10, 1), date(2026, 11, 6))
    assert table.queries == []
