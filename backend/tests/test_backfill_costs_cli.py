"""The operator backfill command (plan 7b): re-imports older usage days for one org."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select, text

from app.cli.backfill_costs import backfill_costs, format_report
from app.models.cost_record import CostRecord
from tests.support.billing_export_table import BillingExportTable, ExportRow

UTC = timezone.utc


async def _configure_export(session):
    for key, value in {
        "billing_export_configured": "true",
        "billing_export_dataset": "billing_export",
        "billing_export_table": "gcp_billing_export_v1_ABC123",
        "gcp_project_id": "test-project",
    }.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=key, v=value)
        )
    await session.commit()


async def _amounts(session, org_id, d):
    rows = (
        (
            await session.execute(
                select(CostRecord).where(CostRecord.organization_id == org_id, CostRecord.record_date == d)
            )
        )
        .scalars()
        .all()
    )
    return {r.component: r.cost_amount for r in rows}


@pytest.mark.asyncio
async def test_backfill_commits_rebuilt_days_and_reports_the_rest(billing_clock, admin_user, session, db_engine):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    billing_clock(datetime(2026, 11, 6, 12, 0, tzinfo=UTC))
    org_id = admin_user.organization_id
    await _configure_export(session)
    for d in (date(2026, 8, 1), date(2026, 8, 2)):
        session.add(CostRecord(organization_id=org_id, record_date=d, component="node", cost_amount=Decimal("10")))
    await session.commit()
    table = BillingExportTable(
        [
            ExportRow(
                "Compute Engine", 4.0, datetime(2026, 8, 1, 9, tzinfo=UTC), "202608", datetime(2026, 8, 2, tzinfo=UTC)
            )
        ]
    )
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    with patch("app.adapters.billing.gcp.bigquery.Client", side_effect=table.client):
        async with factory() as cli_session:
            report = await backfill_costs(cli_session, org_id, date(2026, 8, 1), date(2026, 8, 2))

    await session.rollback()
    assert await _amounts(session, org_id, date(2026, 8, 1)) == {"compute": Decimal("4.0000")}
    assert await _amounts(session, org_id, date(2026, 8, 2)) == {"node": Decimal("10.0000")}
    text_report = format_report(report)
    assert "rebuilt 1 day" in text_report
    assert "2026-08-02" in text_report


@pytest.mark.asyncio
async def test_a_failed_backfill_says_so_and_changes_nothing(billing_clock, admin_user, session):
    billing_clock(datetime(2026, 11, 6, 12, 0, tzinfo=UTC))
    org_id = admin_user.organization_id
    await _configure_export(session)
    session.add(
        CostRecord(organization_id=org_id, record_date=date(2026, 8, 1), component="node", cost_amount=Decimal("10"))
    )
    await session.commit()
    table = BillingExportTable(
        [
            ExportRow(
                "Compute Engine", 4.0, datetime(2026, 8, 1, 9, tzinfo=UTC), "202608", datetime(2026, 8, 2, tzinfo=UTC)
            )
        ]
    )
    table.fail_after = 0

    with patch("app.adapters.billing.gcp.bigquery.Client", side_effect=table.client):
        report = await backfill_costs(session, org_id, date(2026, 8, 1), date(2026, 8, 1))

    assert report.status == "failed"
    assert "failed" in format_report(report)
    assert await _amounts(session, org_id, date(2026, 8, 1)) == {"node": Decimal("10.0000")}
