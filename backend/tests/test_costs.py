import pytest
from decimal import Decimal
from datetime import date

from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_cost_summary(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/costs/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "current_month_spend" in data
    assert "daily_trend" in data
    assert "breakdown_by_component" in data
    assert "monthly_budget" in data
    assert "projected_month_end" in data


@pytest.mark.asyncio
async def test_get_cost_summary_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    response = await client.get(
        "/api/costs/summary",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_cost_history(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/costs/history?start_date=2025-01-01&end_date=2025-12-31",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "records" in data
    assert "total_amount" in data


@pytest.mark.asyncio
async def test_get_budget_config_default(client: AsyncClient, admin_token: str):
    response = await client.get(
        "/api/costs/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["threshold_50_enabled"] is True
    assert data["threshold_80_enabled"] is True
    assert data["threshold_100_enabled"] is True
    assert data["scale_to_zero_on_100"] is False


@pytest.mark.asyncio
async def test_update_budget_config(client: AsyncClient, admin_token: str):
    response = await client.put(
        "/api/costs/budget",
        json={"monthly_budget": "5000.00", "scale_to_zero_on_100": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["monthly_budget"] == "5000.00"
    assert data["scale_to_zero_on_100"] is True


@pytest.mark.asyncio
async def test_update_budget_currency(client: AsyncClient, admin_token: str):
    response = await client.put(
        "/api/costs/budget",
        json={"monthly_budget": "200.00", "currency": "EUR"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["currency"] == "EUR"

    # Verify currency appears in summary
    summary = await client.get(
        "/api/costs/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert summary.json()["currency"] == "EUR"


@pytest.mark.asyncio
async def test_trigger_billing_sync(client: AsyncClient, admin_token: str):
    response = await client.post(
        "/api/costs/sync",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "sync_initiated"


@pytest.mark.asyncio
async def test_cost_summary_with_records(client: AsyncClient, admin_token: str, admin_user, session):
    """Insert cost records for today and verify summary includes them."""
    from app.models.cost_record import CostRecord

    today = date.today()

    for i, component in enumerate(["compute", "storage", "network"]):
        record = CostRecord(
            organization_id=admin_user.organization_id,
            record_date=today,
            component=component,
            cost_amount=Decimal(f"{(i + 1) * 100}.00"),
        )
        session.add(record)
    await session.flush()
    await session.commit()

    response = await client.get(
        "/api/costs/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert float(data["current_month_spend"]) >= 600.0
    assert len(data["breakdown_by_component"]) >= 3


@pytest.mark.asyncio
async def test_cost_history_with_records(client: AsyncClient, admin_token: str, admin_user, session):
    """Insert cost records and verify history returns them."""
    from app.models.cost_record import CostRecord

    record = CostRecord(
        organization_id=admin_user.organization_id,
        record_date=date(2025, 6, 15),
        component="compute",
        cost_amount=Decimal("250.00"),
    )
    session.add(record)
    await session.flush()
    await session.commit()

    response = await client.get(
        "/api/costs/history?start_date=2025-06-01&end_date=2025-06-30",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert float(data["total_amount"]) >= 250.0


@pytest.mark.asyncio
async def test_sync_billing_data_populates_cost_records(admin_user, session):
    """sync_billing_data should create cost_records for node, storage, and compute."""
    from app.models.cost_record import CostRecord
    from app.services.cost_service import CostService

    org_id = admin_user.organization_id
    await CostService.sync_billing_data(session, org_id)
    await session.flush()

    today = date.today()
    from sqlalchemy import select

    result = await session.execute(
        select(CostRecord).where(
            CostRecord.organization_id == org_id,
            CostRecord.record_date == today,
        )
    )
    records = list(result.scalars().all())
    components = {r.component for r in records}

    assert "node" in components, "Expected a 'node' cost record"
    assert "storage" in components, "Expected a 'storage' cost record"
    assert "compute" in components, "Expected a 'compute' cost record"

    for r in records:
        assert r.cost_amount >= 0, f"Cost for {r.component} should be >= 0"

    # Node cost should be > 0 (always-on platform node)
    node_record = next(r for r in records if r.component == "node")
    assert node_record.cost_amount > 0


@pytest.mark.asyncio
async def test_sync_billing_data_is_idempotent(admin_user, session):
    """Running sync twice should not duplicate records for the same day."""
    from app.models.cost_record import CostRecord
    from app.services.cost_service import CostService

    org_id = admin_user.organization_id
    await CostService.sync_billing_data(session, org_id)
    await session.flush()
    await CostService.sync_billing_data(session, org_id)
    await session.flush()

    today = date.today()
    from sqlalchemy import select

    result = await session.execute(
        select(CostRecord).where(
            CostRecord.organization_id == org_id,
            CostRecord.record_date == today,
        )
    )
    records = list(result.scalars().all())
    components = [r.component for r in records]
    # Each component should appear exactly once
    assert components.count("node") == 1
    assert components.count("storage") == 1
    assert components.count("compute") == 1


@pytest.mark.asyncio
async def test_sync_billing_data_only_writes_today(admin_user, session):
    """sync_billing_data should only create records for today, not backfill."""
    from app.models.cost_record import CostRecord
    from app.services.cost_service import CostService

    org_id = admin_user.organization_id
    await CostService.sync_billing_data(session, org_id)
    await session.flush()

    today = date.today()
    from sqlalchemy import select

    result = await session.execute(
        select(CostRecord).where(
            CostRecord.organization_id == org_id,
            CostRecord.component == "node",
        )
    )
    node_records = list(result.scalars().all())
    assert len(node_records) == 1, f"Expected 1 node record (today only), got {len(node_records)}"
    assert node_records[0].record_date == today


@pytest.mark.asyncio
async def test_cost_summary_includes_synced_data(client: AsyncClient, admin_token: str):
    """Summary endpoint should return non-zero spend from synced data."""
    # Trigger sync first
    await client.post(
        "/api/costs/sync",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    response = await client.get(
        "/api/costs/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert float(data["current_month_spend"]) > 0
    assert len(data["breakdown_by_component"]) >= 3

    component_names = [c["component"] for c in data["breakdown_by_component"]]
    assert "node" in component_names
    assert "storage" in component_names
    assert "compute" in component_names


@pytest.mark.asyncio
async def test_budget_threshold_check(admin_user, session):
    """Test budget threshold checking emits no errors."""
    from app.services.cost_service import CostService

    await CostService.check_budget_thresholds(session, admin_user.organization_id)


@pytest.mark.asyncio
async def test_purge_cost_records(client: AsyncClient, admin_token: str, admin_user, session):
    """DELETE /api/costs/records should remove records in the given date range."""
    from app.models.cost_record import CostRecord

    today = date.today()
    yesterday = date(today.year, today.month, today.day - 1) if today.day > 1 else today

    for d in [yesterday, today]:
        session.add(
            CostRecord(
                organization_id=admin_user.organization_id,
                record_date=d,
                component="node",
                cost_amount=Decimal("1.00"),
            )
        )
    await session.flush()
    await session.commit()

    # Purge only yesterday
    response = await client.delete(
        f"/api/costs/records?start_date={yesterday}&end_date={yesterday}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["deleted_count"] >= 1

    # Today's record should still exist
    from sqlalchemy import select

    result = await session.execute(
        select(CostRecord).where(
            CostRecord.organization_id == admin_user.organization_id,
            CostRecord.record_date == today,
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_purge_cost_records_forbidden_for_viewer(client: AsyncClient, viewer_token: str):
    """Viewers should not be able to purge cost records."""
    response = await client.delete(
        "/api/costs/records?start_date=2026-01-01&end_date=2026-12-31",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_local_cost_rates_from_settings(admin_user, session):
    """Sync should use rates from settings, not hardcoded values."""
    from app.models.cost_record import CostRecord
    from app.services.cost_service import CostService

    org_id = admin_user.organization_id
    await CostService.sync_billing_data(session, org_id)
    await session.flush()

    today = date.today()
    from sqlalchemy import select

    result = await session.execute(
        select(CostRecord).where(
            CostRecord.organization_id == org_id,
            CostRecord.record_date == today,
            CostRecord.component == "node",
        )
    )
    node = result.scalar_one()
    # Default setting is 0.01/hr * 24 = 0.24/day
    assert float(node.cost_amount) == pytest.approx(0.24, abs=0.01)
