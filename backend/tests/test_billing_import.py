"""Provider cost imports by UTC usage day (plan 7b).

Each test runs the real sync against a controlled billing-export table
(tests/support/billing_export_table.py) at a fixed billing reference, and checks
the persisted cost records and what the summary, history, and budget checks
report from them.

Replacement rule: a usage day D is replacement-eligible when D <= today - 2 UTC
days. An eligible day with actuals is rebuilt from the provider's complete daily
aggregate; a younger day replaces only the components that have actuals; a day
with no actuals, or any day when the query fails, keeps its records.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.budget_config import BudgetConfig
from app.models.cost_record import CostRecord
from app.services.event_bus import event_bus
from app.services.event_types import BUDGET_THRESHOLD_80
from tests.support.billing_clock import HOST_TIMEZONES
from tests.support.billing_export_table import BillingExportTable, ExportRow

UTC = timezone.utc
# Nov 6 at noon UTC: Nov 4 and earlier are replacement-eligible, Nov 5 is provisional.
NOV_6 = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)

ESTIMATED = {"source": "estimated"}
FULL_DAY = {"source": "actual", "replacement": "full_day"}
PROVISIONAL = {"source": "actual", "replacement": "component"}


def _at(d: date, hour: int = 12) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=UTC)


def _export_row(
    service: str, cost: float, usage_day: date, *, invoice: str | None = None, exported: date | None = None
):
    invoice = invoice or f"{usage_day.year}{usage_day.month:02d}"
    return ExportRow(service, cost, _at(usage_day), invoice, _at(exported or usage_day, 20))


@pytest_asyncio.fixture
async def export_table(session):
    """BigQuery billing export configured and served by a controlled table."""
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
    table = BillingExportTable()
    with patch("app.adapters.billing.gcp.bigquery.Client", side_effect=table.client):
        yield table


@pytest.fixture(autouse=True)
def quiet_adapters(monkeypatch):
    """Today's intraday estimates are zero, so imported totals are exact."""
    from app.services import cost_service

    async def cluster_metrics():
        return SimpleNamespace(node_pools=[])

    async def storage_metrics():
        return SimpleNamespace(total_cost_monthly_usd=0.0)

    monkeypatch.setattr(
        cost_service, "get_compute_adapter", lambda: SimpleNamespace(get_cluster_metrics=cluster_metrics)
    )
    monkeypatch.setattr(
        cost_service, "get_storage_adapter", lambda: SimpleNamespace(get_storage_metrics=storage_metrics)
    )


def _seed(session, org_id: int, rows: list[tuple[date, str, str]], metadata: dict | None = None) -> None:
    for record_date, component, amount in rows:
        session.add(
            CostRecord(
                organization_id=org_id,
                record_date=record_date,
                component=component,
                cost_amount=Decimal(amount),
                metadata_json=dict(metadata or {}),
            )
        )


async def _sync(session, org_id: int):
    from app.services.cost_service import CostService

    report = await CostService.sync_billing_data(session, org_id)
    await session.commit()
    return report


async def _day(session, org_id: int, d: date) -> dict[str, tuple[Decimal, dict]]:
    """component -> (amount, metadata) for one org and usage day; fails on duplicate components."""
    rows = (
        (
            await session.execute(
                select(CostRecord).where(CostRecord.organization_id == org_id, CostRecord.record_date == d)
            )
        )
        .scalars()
        .all()
    )
    components = [r.component for r in rows]
    assert len(components) == len(set(components)), f"duplicate components on {d}: {components}"
    return {r.component: (r.cost_amount, r.metadata_json) for r in rows}


def _amounts(day: dict) -> dict[str, Decimal]:
    return {component: amount for component, (amount, _) in day.items()}


# ---------------------------------------------------------------------------
# Complete usage-day aggregates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_usage_day_billed_in_two_invoice_months_keeps_both(billing_clock, admin_user, session, export_table):
    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    oct_31 = date(2026, 10, 31)
    export_table.rows += [
        _export_row("Compute Engine", 30.0, oct_31, invoice="202610", exported=date(2026, 11, 1)),
        _export_row("Compute Engine", 20.0, oct_31, invoice="202611", exported=date(2026, 11, 2)),
    ]

    await _sync(session, org_id)
    await _sync(session, org_id)

    assert _amounts(await _day(session, org_id, oct_31)) == {"compute": Decimal("50.0000")}


@pytest.mark.asyncio
async def test_prior_month_usage_billed_later_stays_in_its_usage_month(
    billing_clock, admin_user, session, export_table
):
    from app.services.cost_service import CostService

    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    export_table.rows += [
        # October usage on October's invoice, exported after the month rolled over.
        _export_row("Cloud Storage", 7.0, date(2026, 10, 30), invoice="202610", exported=date(2026, 11, 3)),
        # October usage on November's invoice.
        _export_row("Compute Engine", 12.0, date(2026, 10, 30), invoice="202611", exported=date(2026, 11, 4)),
        _export_row("Compute Engine", 3.0, date(2026, 11, 2), exported=date(2026, 11, 3)),
    ]

    await _sync(session, org_id)

    assert _amounts(await _day(session, org_id, date(2026, 10, 30))) == {
        "storage": Decimal("7.0000"),
        "compute": Decimal("12.0000"),
    }
    summary = await CostService.get_cost_summary(session, org_id)
    assert summary["current_month_spend"] == Decimal("3.0000")
    _, history_total = await CostService.get_cost_history(session, org_id, date(2026, 10, 1), date(2026, 10, 31))
    assert history_total == Decimal("19.0000")


# ---------------------------------------------------------------------------
# Replacement rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_missing_component_keeps_its_estimate_until_the_cutoff(
    billing_clock, admin_user, session, export_table
):
    org_id = admin_user.organization_id
    nov_5 = date(2026, 11, 5)
    _seed(
        session, org_id, [(nov_5, "node", "10.00"), (nov_5, "storage", "5.00"), (nov_5, "compute", "40.00")], ESTIMATED
    )
    await session.commit()
    export_table.rows += [
        _export_row("Compute Engine", 38.0, nov_5, exported=date(2026, 11, 6)),
        _export_row("Cloud Storage", 6.0, nov_5, exported=date(2026, 11, 6)),
    ]

    # Nov 5 is one day old: provisional.
    billing_clock(NOV_6)
    await _sync(session, org_id)
    assert await _day(session, org_id, nov_5) == {
        "compute": (Decimal("38.0000"), PROVISIONAL),
        "storage": (Decimal("6.0000"), PROVISIONAL),
        "node": (Decimal("10.0000"), ESTIMATED),
    }

    # Two days old: the provider's aggregate replaces the whole day.
    billing_clock(datetime(2026, 11, 7, 12, 0, tzinfo=UTC))
    await _sync(session, org_id)
    assert await _day(session, org_id, nov_5) == {
        "compute": (Decimal("38.0000"), FULL_DAY),
        "storage": (Decimal("6.0000"), FULL_DAY),
    }


@pytest.mark.asyncio
async def test_an_eligible_legacy_day_is_rebuilt_from_the_provider(billing_clock, admin_user, session, export_table):
    """Rows with empty metadata (estimate or actual, unknowable) are replaced on the first normal sync."""
    from app.services.cost_service import CostService

    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    nov_2 = date(2026, 11, 2)
    _seed(session, org_id, [(nov_2, "compute", "40.00"), (nov_2, "storage", "5.00"), (nov_2, "node", "10.00")])
    await session.commit()
    export_table.rows += [
        _export_row("Compute Engine", 40.0, nov_2, exported=date(2026, 11, 3)),
        _export_row("Cloud Storage", 5.0, nov_2, exported=date(2026, 11, 3)),
    ]

    report = await _sync(session, org_id)

    assert await _day(session, org_id, nov_2) == {
        "compute": (Decimal("40.0000"), FULL_DAY),
        "storage": (Decimal("5.0000"), FULL_DAY),
    }
    _, total = await CostService.get_cost_history(session, org_id, nov_2, nov_2)
    assert total == Decimal("45.0000")
    assert nov_2 in report.rebuilt_days


@pytest.mark.asyncio
@pytest.mark.parametrize("host_tz", HOST_TIMEZONES)
async def test_the_two_day_cutoff_across_a_year_rollover(billing_clock, host_tz, admin_user, session, export_table):
    """At 00:30 UTC on January 1, December 30 is eligible and December 31 is not, on any host."""
    billing_clock(datetime(2027, 1, 1, 0, 30, tzinfo=UTC), host_tz)
    org_id = admin_user.organization_id
    dec_30, dec_31 = date(2026, 12, 30), date(2026, 12, 31)
    _seed(session, org_id, [(dec_30, "node", "10.00"), (dec_31, "node", "10.00")], ESTIMATED)
    await session.commit()
    export_table.rows += [
        _export_row("Compute Engine", 4.0, dec_30, exported=dec_31),
        _export_row("Compute Engine", 4.0, dec_31, exported=dec_31),
    ]

    await _sync(session, org_id)

    assert _amounts(await _day(session, org_id, dec_30)) == {"compute": Decimal("4.0000")}
    assert _amounts(await _day(session, org_id, dec_31)) == {
        "compute": Decimal("4.0000"),
        "node": Decimal("10.0000"),
    }

    # A correction exported later revises the already-eligible day.
    export_table.rows.append(_export_row("Compute Engine", -1.5, dec_30, invoice="202701", exported=date(2027, 1, 1)))
    await _sync(session, org_id)
    assert _amounts(await _day(session, org_id, dec_30)) == {"compute": Decimal("2.5000")}


@pytest.mark.asyncio
async def test_repeating_a_rebuild_is_stable_and_revisions_drop_stale_components(
    billing_clock, admin_user, session, export_table
):
    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    nov_3 = date(2026, 11, 3)
    # Legacy duplicates and a stale component.
    _seed(session, org_id, [(nov_3, "compute", "40.00"), (nov_3, "compute", "40.00"), (nov_3, "other", "9.00")])
    await session.commit()
    export_table.rows += [
        _export_row("Compute Engine", 40.0, nov_3, exported=date(2026, 11, 4)),
        _export_row("Kubernetes Engine", 2.0, nov_3, exported=date(2026, 11, 4)),
    ]

    await _sync(session, org_id)
    first = await _day(session, org_id, nov_3)
    await _sync(session, org_id)
    assert await _day(session, org_id, nov_3) == first
    assert _amounts(first) == {"compute": Decimal("40.0000"), "node": Decimal("2.0000")}

    # A revised export reports that charge under Cloud Storage instead of GKE.
    export_table.rows[1] = _export_row("Cloud Storage", 2.0, nov_3, exported=date(2026, 11, 5))
    await _sync(session, org_id)
    assert _amounts(await _day(session, org_id, nov_3)) == {"compute": Decimal("40.0000"), "storage": Decimal("2.0000")}


# ---------------------------------------------------------------------------
# Missing or failed provider data preserves records
# ---------------------------------------------------------------------------

LEGACY_DAYS = [date(2026, 11, 2), date(2026, 11, 5)]


async def _seed_legacy(session, org_id):
    for d in LEGACY_DAYS:
        _seed(session, org_id, [(d, "compute", "40.00"), (d, "storage", "5.00"), (d, "node", "10.00")])
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["submit", "partial", "unsupported"])
async def test_a_failed_query_changes_nothing(billing_clock, failure, admin_user, session, export_table):
    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    await _seed_legacy(session, org_id)
    export_table.rows += [
        _export_row("Compute Engine", 1.0, LEGACY_DAYS[0], exported=date(2026, 11, 3)),
        _export_row("Compute Engine", 1.0, LEGACY_DAYS[1], exported=date(2026, 11, 6)),
    ]
    if failure == "submit":
        export_table.fail_with = RuntimeError("quota exceeded")
    elif failure == "partial":
        export_table.fail_after = 1
    else:
        export_table.partitioning = "none"

    report = await _sync(session, org_id)

    assert report.status == "failed"
    for d in LEGACY_DAYS:
        assert _amounts(await _day(session, org_id, d)) == {
            "compute": Decimal("40.0000"),
            "storage": Decimal("5.0000"),
            "node": Decimal("10.0000"),
        }


@pytest.mark.asyncio
async def test_days_without_actuals_keep_their_records(billing_clock, admin_user, session, export_table):
    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    await _seed_legacy(session, org_id)
    # Actuals only for an unrelated day, and for the younger legacy day's compute.
    export_table.rows += [
        _export_row("Compute Engine", 3.0, date(2026, 11, 1), exported=date(2026, 11, 2)),
        _export_row("Compute Engine", 38.0, LEGACY_DAYS[1], exported=date(2026, 11, 6)),
    ]

    report = await _sync(session, org_id)

    # The eligible day had no actuals: nothing inferred, nothing removed.
    assert _amounts(await _day(session, org_id, LEGACY_DAYS[0])) == {
        "compute": Decimal("40.0000"),
        "storage": Decimal("5.0000"),
        "node": Decimal("10.0000"),
    }
    assert LEGACY_DAYS[0] in report.days_without_actuals
    # The younger day keeps the components the provider has not reported yet.
    assert _amounts(await _day(session, org_id, LEGACY_DAYS[1])) == {
        "compute": Decimal("38.0000"),
        "storage": Decimal("5.0000"),
        "node": Decimal("10.0000"),
    }


@pytest.mark.asyncio
async def test_a_failed_rebuild_rolls_back_to_the_original_day(billing_clock, admin_user, session, export_table):
    """A write that fails after the day's rows were deleted leaves the original rows and amounts."""
    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    await _seed_legacy(session, org_id)
    export_table.rows += [
        _export_row("Compute Engine", 40.0, LEGACY_DAYS[0], exported=date(2026, 11, 3)),
        # Too large for cost_records.cost_amount: the insert fails after the delete.
        _export_row("Cloud Storage", 1e12, LEGACY_DAYS[0], exported=date(2026, 11, 3)),
    ]

    report = await _sync(session, org_id)

    assert report.status == "failed"
    assert _amounts(await _day(session, org_id, LEGACY_DAYS[0])) == {
        "compute": Decimal("40.0000"),
        "storage": Decimal("5.0000"),
        "node": Decimal("10.0000"),
    }
    # The intraday bridge still ran.
    assert await _day(session, org_id, date(2026, 11, 6))


@pytest.mark.asyncio
async def test_a_rebuild_touches_only_its_org_and_day(billing_clock, admin_user, session, export_table):
    from app.models.organization import Organization

    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    nov_2, nov_3 = date(2026, 11, 2), date(2026, 11, 3)
    _seed(session, org_id, [(nov_2, "node", "10.00"), (nov_3, "node", "10.00")])
    _seed(session, other.id, [(nov_2, "node", "10.00"), (nov_3, "node", "10.00")])
    await session.commit()
    export_table.rows.append(_export_row("Compute Engine", 4.0, nov_2, exported=nov_3))

    await _sync(session, org_id)

    assert _amounts(await _day(session, org_id, nov_2)) == {"compute": Decimal("4.0000")}
    assert _amounts(await _day(session, org_id, nov_3)) == {"node": Decimal("10.0000")}
    assert _amounts(await _day(session, other.id, nov_2)) == {"node": Decimal("10.0000")}
    assert _amounts(await _day(session, other.id, nov_3)) == {"node": Decimal("10.0000")}


# ---------------------------------------------------------------------------
# Refresh window and backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("host_tz", HOST_TIMEZONES)
async def test_routine_sync_refreshes_the_previous_and_current_month(
    billing_clock, host_tz, admin_user, session, export_table
):
    """Just after the UTC month rolls over, October is still refreshed; September is not."""
    billing_clock(datetime(2026, 11, 1, 1, 0, tzinfo=UTC), host_tz)
    org_id = admin_user.organization_id
    oct_20, sep_15 = date(2026, 10, 20), date(2026, 9, 15)
    _seed(session, org_id, [(oct_20, "node", "10.00"), (sep_15, "node", "10.00")])
    await session.commit()
    export_table.rows += [
        _export_row("Compute Engine", 4.0, oct_20, exported=date(2026, 10, 31)),
        _export_row("Compute Engine", 4.0, sep_15, exported=date(2026, 10, 31)),
    ]

    await _sync(session, org_id)

    assert _amounts(await _day(session, org_id, oct_20)) == {"compute": Decimal("4.0000")}
    assert _amounts(await _day(session, org_id, sep_15)) == {"node": Decimal("10.0000")}
    [(_, params)] = export_table.queries
    assert params["usage_start"] == datetime(2026, 10, 1, tzinfo=UTC)
    assert params["partition_start"] == datetime(2026, 10, 1, tzinfo=UTC)
    assert params["usage_end"] == datetime(2026, 11, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_backfill_repairs_older_days_through_the_same_import(billing_clock, admin_user, session, export_table):
    from app.models.organization import Organization
    from app.services.cost_service import CostService

    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    sep_15, sep_16 = date(2026, 9, 15), date(2026, 9, 16)
    _seed(session, org_id, [(sep_15, "node", "10.00"), (sep_16, "node", "10.00")])
    _seed(session, other.id, [(sep_15, "node", "10.00")])
    await session.commit()
    export_table.rows.append(_export_row("Compute Engine", 4.0, sep_15, exported=date(2026, 10, 2)))

    report = await CostService.backfill_billing_data(session, org_id, sep_15, sep_16)
    await session.commit()

    assert _amounts(await _day(session, org_id, sep_15)) == {"compute": Decimal("4.0000")}
    # No provider history for Sep 16: kept and reported as not reconstructable.
    assert _amounts(await _day(session, org_id, sep_16)) == {"node": Decimal("10.0000")}
    assert report.days_without_actuals == [sep_16]
    assert report.rebuilt_days == [sep_15]
    assert _amounts(await _day(session, other.id, sep_15)) == {"node": Decimal("10.0000")}
    [(_, params)] = export_table.queries
    assert params["partition_start"] == datetime(2026, 9, 15, tzinfo=UTC)
    assert params["usage_end"] == datetime(2026, 9, 17, tzinfo=UTC)


@pytest.mark.asyncio
async def test_backfill_never_reaches_today(billing_clock, admin_user, session, export_table):
    from app.exceptions import ValidationError
    from app.services.cost_service import CostService

    billing_clock(NOV_6)
    org_id = admin_user.organization_id

    report = await CostService.backfill_billing_data(session, org_id, date(2026, 11, 1), date(2026, 11, 30))
    await session.commit()
    [(_, params)] = export_table.queries
    assert params["usage_end"] == datetime(2026, 11, 6, tzinfo=UTC)
    assert report.usage_end == date(2026, 11, 6)

    with pytest.raises(ValidationError):
        await CostService.backfill_billing_data(session, org_id, date(2026, 11, 6), date(2026, 11, 6))
    with pytest.raises(ValidationError):
        await CostService.backfill_billing_data(session, org_id, date(2026, 11, 3), date(2026, 11, 2))


@pytest.mark.asyncio
async def test_backfill_requires_a_configured_export(billing_clock, admin_user, session):
    from app.exceptions import ValidationError
    from app.services.cost_service import CostService

    billing_clock(NOV_6)
    with pytest.raises(ValidationError):
        await CostService.backfill_billing_data(
            session, admin_user.organization_id, date(2026, 9, 1), date(2026, 9, 30)
        )


# ---------------------------------------------------------------------------
# One reference for the provider window and the intraday bridge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("host_tz", HOST_TIMEZONES)
async def test_provider_window_and_estimates_share_one_utc_reference(
    billing_clock, host_tz, admin_user, session, export_table
):
    """03:00 UTC on November 1 is still October 31 in Pacific time; the UTC usage month is November."""
    from app.services.cost_service import CostService

    billing_clock(
        [datetime(2026, 11, 1, 3, 0, tzinfo=UTC), datetime(2026, 11, 2, 3, 0, tzinfo=UTC)],
        host_tz,
    )
    org_id = admin_user.organization_id
    export_table.rows += [
        ExportRow(
            "Compute Engine", 6.0, datetime(2026, 10, 31, 23, 30, tzinfo=UTC), "202610", _at(date(2026, 11, 1), 2)
        ),
        # Usage today (UTC) is left to the intraday estimate.
        ExportRow("Compute Engine", 9.0, datetime(2026, 11, 1, 1, 0, tzinfo=UTC), "202611", _at(date(2026, 11, 1), 2)),
    ]

    summary = await CostService.get_cost_summary(session, org_id)

    [(_, params)] = export_table.queries
    assert params["usage_start"] == datetime(2026, 10, 1, tzinfo=UTC)
    assert params["usage_end"] == datetime(2026, 11, 1, tzinfo=UTC)
    await session.rollback()
    assert _amounts(await _day(session, org_id, date(2026, 10, 31))) == {"compute": Decimal("6.0000")}
    today = await _day(session, org_id, date(2026, 11, 1))
    assert {meta["source"] for _, meta in today.values()} == {"estimated"}
    assert summary["current_month_spend"] == Decimal("0")
    assert [d["date"] for d in summary["daily_trend"]] == ["2026-11-01"]


# ---------------------------------------------------------------------------
# What corrected imports change for budgets
# ---------------------------------------------------------------------------


@pytest.fixture
def budget_events(monkeypatch):
    seen: list[str] = []

    async def _emit(event_type, payload):
        seen.append(event_type)

    monkeypatch.setattr(event_bus, "emit", _emit)
    return seen


@pytest.mark.asyncio
async def test_an_understated_split_day_now_reaches_its_threshold(
    billing_clock, admin_user, session, export_table, budget_events
):
    """One invoice slice alone ($20 of $50) would have been 36% of a $55 budget."""
    from app.services.cost_service import CostService

    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    session.add(BudgetConfig(organization_id=org_id, monthly_budget=Decimal("55.00")))
    await session.commit()
    nov_2 = date(2026, 11, 2)
    export_table.rows += [
        _export_row("Compute Engine", 30.0, nov_2, invoice="202610", exported=date(2026, 11, 3)),
        _export_row("Compute Engine", 20.0, nov_2, invoice="202611", exported=date(2026, 11, 3)),
    ]

    await _sync(session, org_id)
    await CostService.check_budget_thresholds(session, org_id)
    for _ in range(5):
        await asyncio.sleep(0)

    summary = await CostService.get_cost_summary(session, org_id)
    assert summary["current_month_spend"] == Decimal("50.0000")
    assert budget_events == [BUDGET_THRESHOLD_80]


@pytest.mark.asyncio
async def test_an_overstated_stale_estimate_no_longer_counts(
    billing_clock, admin_user, session, export_table, budget_events
):
    """A surviving $50 node estimate would have put spend at 95% of $100."""
    from app.services.cost_service import CostService

    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    session.add(BudgetConfig(organization_id=org_id, monthly_budget=Decimal("100.00")))
    nov_2 = date(2026, 11, 2)
    _seed(session, org_id, [(nov_2, "compute", "40.00"), (nov_2, "storage", "5.00"), (nov_2, "node", "50.00")])
    await session.commit()
    export_table.rows += [
        _export_row("Compute Engine", 40.0, nov_2, exported=date(2026, 11, 3)),
        _export_row("Cloud Storage", 5.0, nov_2, exported=date(2026, 11, 3)),
    ]

    await _sync(session, org_id)
    await CostService.check_budget_thresholds(session, org_id)
    for _ in range(5):
        await asyncio.sleep(0)

    summary = await CostService.get_cost_summary(session, org_id)
    assert summary["current_month_spend"] == Decimal("45.0000")
    assert budget_events == []


# ---------------------------------------------------------------------------
# Atomicity under the per-org lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rebuild_is_invisible_until_it_commits(billing_clock, admin_user, session, db_engine, export_table):
    """Another reader sees the original day, never an empty or half-rebuilt one, until the sync commits."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.services.cost_service import CostService

    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    await _seed_legacy(session, org_id)
    export_table.rows += [
        _export_row("Compute Engine", 4.0, LEGACY_DAYS[0], exported=date(2026, 11, 3)),
        _export_row("Cloud Storage", 1.0, LEGACY_DAYS[0], exported=date(2026, 11, 3)),
    ]
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as writer, factory() as reader:
        await CostService.sync_billing_data(writer, org_id)
        assert _amounts(await _day(reader, org_id, LEGACY_DAYS[0])) == {
            "compute": Decimal("40.0000"),
            "storage": Decimal("5.0000"),
            "node": Decimal("10.0000"),
        }
        await writer.commit()
        await reader.rollback()
        assert _amounts(await _day(reader, org_id, LEGACY_DAYS[0])) == {
            "compute": Decimal("4.0000"),
            "storage": Decimal("1.0000"),
        }


@pytest.mark.asyncio
async def test_a_backfill_waits_for_a_running_sync(billing_clock, admin_user, db_engine, export_table):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.services.cost_service import CostService

    billing_clock(NOV_6)
    org_id = admin_user.organization_id
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as first, factory() as second:
        await CostService.sync_billing_data(first, org_id)

        task = asyncio.create_task(
            CostService.backfill_billing_data(second, org_id, date(2026, 9, 1), date(2026, 9, 30))
        )
        await asyncio.sleep(0.5)
        assert not task.done(), "a backfill ran while a sync for the same org was still open"

        await first.commit()
        await asyncio.wait_for(task, timeout=10)
        await second.commit()
