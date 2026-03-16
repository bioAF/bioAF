"""Cost center service with billing sync, budget config, and threshold checks."""

import asyncio
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_compute_adapter, get_storage_adapter
from app.models.budget_config import BudgetConfig
from app.models.cost_record import CostRecord
from app.services.event_bus import event_bus
from app.services.event_types import BUDGET_THRESHOLD_50, BUDGET_THRESHOLD_80, BUDGET_THRESHOLD_100

logger = logging.getLogger("bioaf.cost_service")


class CostService:
    @staticmethod
    async def get_cost_summary(session: AsyncSession, org_id: int) -> dict:
        """Get current month cost summary with trends and breakdown."""
        # Lazy sync: if no records exist for today, sync from adapters first
        today = date.today()
        today_check = await session.execute(
            select(func.count())
            .select_from(CostRecord)
            .where(
                CostRecord.organization_id == org_id,
                CostRecord.record_date == today,
            )
        )
        if (today_check.scalar() or 0) == 0:
            await CostService.sync_billing_data(session, org_id)
            await session.flush()

        now = datetime.now(timezone.utc)
        month_start = date(now.year, now.month, 1)

        # Current month total
        total_result = await session.execute(
            select(func.coalesce(func.sum(CostRecord.cost_amount), 0)).where(
                CostRecord.organization_id == org_id,
                CostRecord.record_date >= month_start,
            )
        )
        current_month_spend = total_result.scalar() or Decimal("0")

        # Daily trend
        daily_result = await session.execute(
            select(
                CostRecord.record_date,
                func.sum(CostRecord.cost_amount).label("amount"),
            )
            .where(
                CostRecord.organization_id == org_id,
                CostRecord.record_date >= month_start,
            )
            .group_by(CostRecord.record_date)
            .order_by(CostRecord.record_date)
        )
        daily_trend = [{"date": str(row[0]), "amount": row[1]} for row in daily_result.all()]

        # Breakdown by component
        component_result = await session.execute(
            select(
                CostRecord.component,
                func.sum(CostRecord.cost_amount).label("amount"),
            )
            .where(
                CostRecord.organization_id == org_id,
                CostRecord.record_date >= month_start,
            )
            .group_by(CostRecord.component)
            .order_by(func.sum(CostRecord.cost_amount).desc())
        )
        total_float = float(current_month_spend) if current_month_spend else 1.0
        breakdown = [
            {
                "component": row[0],
                "amount": row[1],
                "percentage": round(float(row[1]) / total_float * 100, 1) if total_float > 0 else 0,
            }
            for row in component_result.all()
        ]

        # Budget
        budget = await CostService.get_budget_config(session, org_id)
        monthly_budget = budget.monthly_budget if budget else None
        budget_remaining = (monthly_budget - current_month_spend) if monthly_budget else None

        # Projection (linear extrapolation)
        days_elapsed = (now.date() - month_start).days + 1
        if now.month == 12:
            days_in_month = 31
        else:
            next_month = date(now.year, now.month + 1, 1)
            days_in_month = (next_month - month_start).days
        projected = (current_month_spend / days_elapsed * days_in_month) if days_elapsed > 0 else Decimal("0")

        currency = budget.currency if budget else "USD"

        return {
            "current_month_spend": current_month_spend,
            "daily_trend": daily_trend,
            "breakdown_by_component": breakdown,
            "per_user": [],
            "monthly_budget": monthly_budget,
            "budget_remaining": budget_remaining,
            "projected_month_end": projected,
            "currency": currency,
        }

    @staticmethod
    async def get_cost_history(
        session: AsyncSession,
        org_id: int,
        start_date: date,
        end_date: date,
    ) -> tuple[list[dict], Decimal]:
        result = await session.execute(
            select(
                CostRecord.record_date,
                func.sum(CostRecord.cost_amount).label("amount"),
            )
            .where(
                CostRecord.organization_id == org_id,
                CostRecord.record_date >= start_date,
                CostRecord.record_date <= end_date,
            )
            .group_by(CostRecord.record_date)
            .order_by(CostRecord.record_date)
        )
        records = [{"date": str(row[0]), "amount": row[1]} for row in result.all()]
        total = sum(r["amount"] for r in records) if records else Decimal("0")
        return records, total

    @staticmethod
    async def sync_billing_data(session: AsyncSession, org_id: int) -> None:
        """Sync today's cost data from infrastructure adapters into cost_records.

        Records daily costs for three components:
        - node: the always-on bioAF platform VM
        - storage: all GCS buckets
        - compute: pipeline and interactive compute nodes

        Only writes today's record. Past records accumulate from prior daily
        syncs so costs reflect what was actually running each day.
        Idempotent -- re-syncing the same day updates existing records.
        """
        logger.info("Syncing billing data for org %d", org_id)
        today = date.today()
        month_start = date(today.year, today.month, 1)

        # -- Node and compute cost from cluster metrics --
        # Cluster metrics reflect actual billing rates (sustained-use discounts,
        # committed-use, free-tier credits) rather than GCP list prices.
        compute_adapter = get_compute_adapter()
        cluster_metrics = await compute_adapter.get_cluster_metrics()
        node_cost_daily = Decimal("0")
        compute_cost_daily = Decimal("0")

        for pool in cluster_metrics.get("node_pools", []):
            hourly = Decimal(str(pool.get("cost_rate_hourly", 0)))
            if pool.get("name", "") == "bioaf-platform":
                node_cost_daily += hourly * 24
            else:
                compute_cost_daily += hourly * 24

        # -- Storage cost (prorated daily from monthly) --
        storage_adapter = get_storage_adapter()
        storage_metrics = await storage_adapter.get_storage_metrics()
        storage_cost_monthly = Decimal(str(storage_metrics.get("total_cost_monthly_usd", 0)))
        if today.month == 12:
            days_in_month = 31
        else:
            next_month = date(today.year, today.month + 1, 1)
            days_in_month = (next_month - month_start).days
        storage_cost_daily = storage_cost_monthly / days_in_month if days_in_month > 0 else Decimal("0")

        # -- Upsert today's cost records --
        components = {
            "node": node_cost_daily,
            "storage": storage_cost_daily,
            "compute": compute_cost_daily,
        }

        for component, amount in components.items():
            existing_rec = await session.execute(
                select(CostRecord).where(
                    CostRecord.organization_id == org_id,
                    CostRecord.record_date == today,
                    CostRecord.component == component,
                )
            )
            record = existing_rec.scalar_one_or_none()
            if record:
                record.cost_amount = amount
            else:
                session.add(
                    CostRecord(
                        organization_id=org_id,
                        record_date=today,
                        component=component,
                        cost_amount=amount,
                    )
                )

    @staticmethod
    async def get_budget_config(session: AsyncSession, org_id: int) -> BudgetConfig | None:
        result = await session.execute(select(BudgetConfig).where(BudgetConfig.organization_id == org_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def update_budget_config(
        session: AsyncSession,
        org_id: int,
        data: dict,
    ) -> BudgetConfig:
        result = await session.execute(select(BudgetConfig).where(BudgetConfig.organization_id == org_id))
        config = result.scalar_one_or_none()

        if not config:
            config = BudgetConfig(organization_id=org_id)
            session.add(config)

        for key in [
            "monthly_budget",
            "threshold_50_enabled",
            "threshold_80_enabled",
            "threshold_100_enabled",
            "scale_to_zero_on_100",
            "currency",
        ]:
            if key in data and data[key] is not None:
                setattr(config, key, data[key])

        config.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return config

    @staticmethod
    async def check_budget_thresholds(session: AsyncSession, org_id: int) -> None:
        """Compare current spend to budget, emit events for crossed thresholds."""
        config = await CostService.get_budget_config(session, org_id)
        if not config or not config.monthly_budget:
            return

        now = datetime.now(timezone.utc)
        month_start = date(now.year, now.month, 1)

        total_result = await session.execute(
            select(func.coalesce(func.sum(CostRecord.cost_amount), 0)).where(
                CostRecord.organization_id == org_id,
                CostRecord.record_date >= month_start,
            )
        )
        current_spend = total_result.scalar() or Decimal("0")
        usage_pct = float(current_spend) / float(config.monthly_budget) * 100

        if usage_pct >= 100 and config.threshold_100_enabled:
            asyncio.create_task(
                event_bus.emit(
                    BUDGET_THRESHOLD_100,
                    {
                        "event_type": BUDGET_THRESHOLD_100,
                        "org_id": org_id,
                        "title": "Budget limit reached (100%)",
                        "message": f"Spend ${current_spend:.2f} has reached the ${config.monthly_budget:.2f} monthly budget",
                        "severity": "critical",
                        "summary": f"Monthly budget 100% reached: ${current_spend:.2f}/${config.monthly_budget:.2f}",
                    },
                )
            )
        elif usage_pct >= 80 and config.threshold_80_enabled:
            asyncio.create_task(
                event_bus.emit(
                    BUDGET_THRESHOLD_80,
                    {
                        "event_type": BUDGET_THRESHOLD_80,
                        "org_id": org_id,
                        "title": f"Budget at {usage_pct:.0f}%",
                        "message": f"Spend ${current_spend:.2f} of ${config.monthly_budget:.2f} monthly budget",
                        "severity": "warning",
                        "summary": f"Monthly budget at {usage_pct:.0f}%",
                    },
                )
            )
        elif usage_pct >= 50 and config.threshold_50_enabled:
            asyncio.create_task(
                event_bus.emit(
                    BUDGET_THRESHOLD_50,
                    {
                        "event_type": BUDGET_THRESHOLD_50,
                        "org_id": org_id,
                        "title": f"Budget at {usage_pct:.0f}%",
                        "message": f"Spend ${current_spend:.2f} of ${config.monthly_budget:.2f} monthly budget",
                        "severity": "info",
                        "summary": f"Monthly budget at {usage_pct:.0f}%",
                    },
                )
            )
