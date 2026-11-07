"""Cost operations report by UTC usage date, from one captured reference (plan 7a).

``record_date`` is a usage day. A usage month is [month_start, next_month_start)
of the UTC calendar month containing the operation's reference instant, whatever
timezone the host runs in. Each test fixes that instant and the host timezone
(tests/support/billing_clock.py), so none depends on the wall clock.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.budget_config import BudgetConfig
from app.models.cost_record import CostRecord
from app.services.event_bus import event_bus
from app.services.event_types import BUDGET_THRESHOLD_50, BUDGET_THRESHOLD_80, BUDGET_THRESHOLD_100
from tests.support.billing_clock import HOST_TIMEZONES

# 20:00 UTC on October 31: already November 1 in Tokyo, still October 31 in Honolulu.
OCT_31_EVENING = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)


def _seed(session, org_id: int, rows: list[tuple[date, str, str]]) -> None:
    for record_date, component, amount in rows:
        session.add(
            CostRecord(
                organization_id=org_id,
                record_date=record_date,
                component=component,
                cost_amount=Decimal(amount),
            )
        )


@pytest.fixture
def budget_events(monkeypatch):
    seen: list[str] = []

    async def _emit(event_type, payload):
        seen.append(event_type)

    monkeypatch.setattr(event_bus, "emit", _emit)
    return seen


async def _drain_tasks() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _storage_adapter(monthly_usd: float):
    async def get_storage_metrics():
        return SimpleNamespace(total_cost_monthly_usd=monthly_usd)

    return SimpleNamespace(get_storage_metrics=get_storage_metrics)


@pytest.mark.asyncio
@pytest.mark.parametrize("host_tz", HOST_TIMEZONES)
async def test_summary_counts_only_the_utc_usage_month(billing_clock, host_tz, admin_user, session):
    """Month start is in; the previous month and the next month start are out."""
    from app.services.cost_service import CostService

    billing_clock(OCT_31_EVENING, host_tz)
    org_id = admin_user.organization_id
    _seed(
        session,
        org_id,
        [
            (date(2026, 9, 30), "compute", "100.00"),
            (date(2026, 10, 1), "compute", "10.00"),
            (date(2026, 10, 31), "storage", "1.00"),
            (date(2026, 11, 1), "compute", "1000.00"),
        ],
    )
    await session.commit()

    summary = await CostService.get_cost_summary(session, org_id)

    assert summary["current_month_spend"] == Decimal("11.00")
    assert [d["date"] for d in summary["daily_trend"]] == ["2026-10-01", "2026-10-31"]
    assert {b["component"]: b["amount"] for b in summary["breakdown_by_component"]} == {
        "compute": Decimal("10.00"),
        "storage": Decimal("1.00"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("host_tz", HOST_TIMEZONES)
async def test_thresholds_count_only_the_utc_usage_month(billing_clock, host_tz, admin_user, session, budget_events):
    from app.services.cost_service import CostService

    billing_clock(OCT_31_EVENING, host_tz)
    org_id = admin_user.organization_id
    _seed(
        session,
        org_id,
        [
            (date(2026, 9, 30), "compute", "100.00"),
            (date(2026, 10, 1), "compute", "10.00"),
            (date(2026, 10, 31), "storage", "1.00"),
            (date(2026, 11, 1), "compute", "1000.00"),
        ],
    )
    session.add(BudgetConfig(organization_id=org_id, monthly_budget=Decimal("20.00")))
    await session.commit()

    await CostService.check_budget_thresholds(session, org_id)
    await _drain_tasks()

    # 11.00 of 20.00 is 55%.
    assert budget_events == [BUDGET_THRESHOLD_50]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference, days_in_month",
    [
        (datetime(2028, 2, 10, 12, 0, tzinfo=timezone.utc), 29),
        (datetime(2027, 2, 10, 12, 0, tzinfo=timezone.utc), 28),
        (datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc), 31),
    ],
)
async def test_projection_uses_the_utc_month_length(billing_clock, reference, days_in_month, admin_user, session):
    from app.services.cost_service import CostService

    billing_clock(reference, "Pacific/Honolulu")
    org_id = admin_user.organization_id
    _seed(session, org_id, [(reference.date(), "compute", "10.00")])
    await session.commit()

    summary = await CostService.get_cost_summary(session, org_id)

    # 10.00 over 10 elapsed days, extrapolated to the month.
    assert summary["projected_month_end"] == Decimal(days_in_month)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference, days_in_month",
    [
        (datetime(2028, 2, 10, 12, 0, tzinfo=timezone.utc), 29),
        (datetime(2027, 2, 10, 12, 0, tzinfo=timezone.utc), 28),
        (datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc), 31),
    ],
)
async def test_storage_estimate_is_allocated_over_the_utc_month(
    billing_clock, monkeypatch, reference, days_in_month, admin_user, session
):
    from app.services import cost_service
    from app.services.cost_service import CostService

    billing_clock(reference, "Asia/Tokyo")
    monkeypatch.setattr(cost_service, "get_storage_adapter", lambda: _storage_adapter(float(days_in_month) * 3))
    org_id = admin_user.organization_id

    await CostService.sync_billing_data(session, org_id)
    await session.flush()

    storage = (
        await session.execute(
            select(CostRecord).where(CostRecord.organization_id == org_id, CostRecord.component == "storage")
        )
    ).scalar_one()
    assert storage.record_date == reference.date()
    assert storage.cost_amount == Decimal("3.0000")


@pytest.mark.asyncio
@pytest.mark.parametrize("host_tz", HOST_TIMEZONES)
async def test_summary_crossing_midnight_keeps_one_period(billing_clock, host_tz, admin_user, session):
    """The lazy sync and the summary read one captured period, even across a month boundary."""
    from app.services.cost_service import CostService

    billing_clock(
        [
            datetime(2026, 10, 31, 23, 59, 59, 999000, tzinfo=timezone.utc),
            datetime(2026, 11, 1, 0, 0, 0, 1000, tzinfo=timezone.utc),
        ],
        host_tz,
    )
    org_id = admin_user.organization_id

    summary = await CostService.get_cost_summary(session, org_id)

    await session.rollback()
    rows = list((await session.execute(select(CostRecord).where(CostRecord.organization_id == org_id))).scalars().all())
    assert rows, "the lazy sync wrote nothing"
    assert {r.record_date for r in rows} == {date(2026, 10, 31)}
    assert [d["date"] for d in summary["daily_trend"]] == ["2026-10-31"]
    assert summary["current_month_spend"] == sum(r.cost_amount for r in rows)
    assert summary["current_month_spend"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("host_tz", HOST_TIMEZONES)
async def test_daily_pass_syncs_and_checks_every_org_in_one_period(
    billing_clock, host_tz, admin_user, session, budget_events
):
    """A pass that starts before midnight writes and evaluates every org in the period it started in."""
    from app.main import _cost_billing_sync_once
    from app.models.organization import Organization

    billing_clock(
        [
            datetime(2026, 10, 31, 23, 59, 59, 999000, tzinfo=timezone.utc),
            datetime(2026, 11, 1, 0, 0, 0, 1000, tzinfo=timezone.utc),
        ],
        host_tz,
    )
    other = Organization(name="Other Org", setup_complete=True)
    session.add(other)
    await session.flush()
    org_ids = [admin_user.organization_id, other.id]
    for org_id in org_ids:
        session.add(BudgetConfig(organization_id=org_id, monthly_budget=Decimal("0.01")))
    await session.commit()

    await _cost_billing_sync_once()
    await _drain_tasks()

    await session.rollback()
    for org_id in org_ids:
        dates = set(
            (await session.execute(select(CostRecord.record_date).where(CostRecord.organization_id == org_id)))
            .scalars()
            .all()
        )
        assert dates == {date(2026, 10, 31)}
    # Each org's synced October spend exceeds its 0.01 budget.
    assert budget_events == [BUDGET_THRESHOLD_100, BUDGET_THRESHOLD_100]
    assert BUDGET_THRESHOLD_80 not in budget_events
