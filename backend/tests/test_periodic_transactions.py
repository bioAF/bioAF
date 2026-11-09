"""A failed iteration step leaves a usable transaction for the next.

Per-organization jobs commit each organization on its own. One organization's failed write must
not poison the session for the organizations after it.
"""

import pytest
from sqlalchemy import select

from app.models.component import PlatformConfig
from app.models.organization import Organization
from app.tasks import jobs
from app.tasks.periodic import run_periodic

pytestmark = pytest.mark.asyncio


def _one_iteration():
    calls = {"n": 0}

    async def wait(delay, stopping):
        calls["n"] += 1
        return calls["n"] > 1

    return wait


async def test_one_organizations_failed_storage_refresh_does_not_poison_the_next(session, monkeypatch):
    from app.services.storage_service import StorageService

    first, second = Organization(name="First"), Organization(name="Second")
    session.add_all([first, second, PlatformConfig(key="taken", value="x")])
    await session.commit()
    order = [first.id, second.id]

    async def refresh(s, org_id):
        if org_id == order[0]:
            s.add(PlatformConfig(key="taken", value="duplicate"))
            await s.flush()
        else:
            s.add(PlatformConfig(key=f"refreshed-{org_id}", value="ok"))
            await s.flush()
        return {}

    monkeypatch.setattr(StorageService, "refresh_storage_stats", staticmethod(refresh))
    job = next(j for j in jobs.PERIODIC_JOBS if j.role == "storage-stats-refresh")
    await run_periodic(job, wait=_one_iteration())

    await session.rollback()
    row = (await session.execute(select(PlatformConfig).where(PlatformConfig.key == f"refreshed-{second.id}"))).scalar()
    assert row is not None


async def test_one_organizations_failed_billing_sync_is_reported_and_the_next_still_syncs(session, monkeypatch, caplog):
    import logging

    from app.services.cost_service import CostService

    first, second = Organization(name="First"), Organization(name="Second")
    session.add_all([first, second, PlatformConfig(key="taken", value="x")])
    await session.commit()
    failing, other = first.id, second.id

    async def sync(s, org_id, period):
        key = "taken" if org_id == failing else f"billed-{org_id}"
        s.add(PlatformConfig(key=key, value="v"))
        await s.flush()
        return None

    async def thresholds(s, org_id, period):
        return None

    monkeypatch.setattr(CostService, "sync_billing_data", staticmethod(sync))
    monkeypatch.setattr(CostService, "check_budget_thresholds", staticmethod(thresholds))
    with caplog.at_level(logging.WARNING, logger="bioaf"):
        await jobs.cost_billing_sync_once()

    await session.rollback()
    assert (await session.execute(select(PlatformConfig).where(PlatformConfig.key == f"billed-{other}"))).scalar()
    assert f"Billing sync failed for org {failing}:" in caplog.text
