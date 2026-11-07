"""Cost center service with billing sync, budget config, and threshold checks."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_compute_adapter, get_storage_adapter
from app.models.budget_config import BudgetConfig
from app.models.cost_record import CostRecord
from app.services.billing_period import BillingPeriod, billing_period
from app.services.event_bus import event_bus
from app.services.event_types import BUDGET_THRESHOLD_50, BUDGET_THRESHOLD_80, BUDGET_THRESHOLD_100

logger = logging.getLogger("bioaf.cost_service")

# Arbitrary but stable namespace for the per-org billing-sync advisory lock.
_COST_SYNC_LOCK_NAMESPACE = 4242


def _in_usage_month(org_id: int, period: BillingPeriod) -> tuple:
    """Rows for ``org_id`` whose usage date is in the period's UTC month."""
    return (
        CostRecord.organization_id == org_id,
        CostRecord.record_date >= period.month_start,
        CostRecord.record_date < period.next_month_start,
    )


async def _usage_month_spend(session: AsyncSession, org_id: int, period: BillingPeriod) -> Decimal:
    result = await session.execute(
        select(func.coalesce(func.sum(CostRecord.cost_amount), 0)).where(*_in_usage_month(org_id, period))
    )
    return result.scalar() or Decimal("0")


# A usage day this many UTC days old or older is replacement-eligible: its
# records are rebuilt from the provider's daily aggregate. An application
# heuristic, not a provider finality guarantee; later corrections still apply.
_REPLACEMENT_AGE = timedelta(days=2)


@dataclass
class UsageImport:
    """What one provider import over ``[usage_start, usage_end)`` did."""

    usage_start: date
    usage_end: date
    status: str = "imported"  # "imported", "failed", or "not_configured"
    rebuilt_days: list[date] = field(default_factory=list)
    provisional_days: list[date] = field(default_factory=list)
    # Kept as they were; for an older backfill these are not reconstructable.
    days_without_actuals: list[date] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "usage_start": self.usage_start.isoformat(),
            "usage_end": self.usage_end.isoformat(),
            "rebuilt_days": [d.isoformat() for d in self.rebuilt_days],
            "provisional_days": [d.isoformat() for d in self.provisional_days],
            "days_without_actuals": [d.isoformat() for d in self.days_without_actuals],
        }


async def _billing_export_config(session: AsyncSession) -> dict | None:
    """The billing-export platform config, or None when the export is not configured."""
    from app.platform.platform_config_service import PlatformConfigService

    bq_config = await PlatformConfigService.get_many(
        session,
        [
            "billing_export_configured",
            "billing_export_dataset",
            "billing_export_table",
            "gcp_project_id",
            "gcp_credential_source",
            "gcp_service_account_key",
            "gcp_service_account_email",
        ],
    )
    if bq_config.get("billing_export_configured", "false") != "true":
        return None
    return bq_config


async def _import_usage(
    session: AsyncSession,
    org_id: int,
    bq_config: dict,
    usage_start: date,
    usage_end: date,
    period: BillingPeriod,
) -> UsageImport:
    """Import provider actuals for UTC usage days in ``[usage_start, usage_end)``.

    The whole result is read before anything is written. For each usage day the
    provider returned actuals for: an eligible day (``_REPLACEMENT_AGE`` or
    older) has all of its records replaced by the provider's component
    aggregates, including legacy rows with empty metadata; a younger day has
    only the returned components replaced, so estimates for components without
    actuals yet still count. Days without actuals, and every day when the query
    fails, keep their records: missing export data is not a zero-cost day.
    All replacements happen in one savepoint, so a failed write leaves every
    day as it was.
    """
    from app.services.billing_export_service import BillingExportService

    report = UsageImport(usage_start=usage_start, usage_end=usage_end)
    project_id = bq_config.get("gcp_project_id", "")
    dataset_id = bq_config.get("billing_export_dataset", "")
    table_id = bq_config.get("billing_export_table", "")
    if not (project_id and dataset_id and table_id):
        report.status = "not_configured"
        return report

    try:
        from app.adapters.credentials.credential_injector import load_gcp_credentials

        try:
            creds = load_gcp_credentials(bq_config)
        except Exception:
            logger.warning("Failed to load GCP credentials for BQ sync, using ADC")
            creds = None
        rows = await BillingExportService.query_usage_costs(
            project_id, dataset_id, table_id, usage_start, usage_end, credentials=creds
        )
    except Exception:
        logger.exception("Failed to query BQ billing export; existing cost records kept")
        report.status = "failed"
        return report

    daily: dict[date, dict[str, Decimal]] = {}
    for row in rows:
        usage_date = row["usage_date"]
        if not usage_start <= usage_date < usage_end:
            continue
        components = daily.setdefault(usage_date, {})
        components[row["component"]] = components.get(row["component"], Decimal("0")) + Decimal(str(row["net_cost"]))

    cutoff = period.today - _REPLACEMENT_AGE
    rebuilt: list[date] = []
    provisional: list[date] = []
    try:
        async with session.begin_nested():
            for usage_date, components in sorted(daily.items()):
                stale = delete(CostRecord).where(
                    CostRecord.organization_id == org_id,
                    CostRecord.record_date == usage_date,
                )
                if usage_date <= cutoff:
                    replacement = "full_day"
                    rebuilt.append(usage_date)
                else:
                    stale = stale.where(CostRecord.component.in_(components))
                    replacement = "component"
                    provisional.append(usage_date)
                await session.execute(stale)
                session.add_all(
                    CostRecord(
                        organization_id=org_id,
                        record_date=usage_date,
                        component=component,
                        cost_amount=amount,
                        metadata_json={"source": "actual", "replacement": replacement},
                    )
                    for component, amount in components.items()
                )
            await session.flush()
    except Exception:
        logger.exception("Failed to store BQ billing export costs; existing cost records kept")
        report.status = "failed"
        return report

    report.rebuilt_days = rebuilt
    report.provisional_days = provisional
    report.days_without_actuals = [
        usage_start + timedelta(days=i)
        for i in range((usage_end - usage_start).days)
        if usage_start + timedelta(days=i) not in daily
    ]
    return report


class CostService:
    @staticmethod
    async def get_cost_summary(session: AsyncSession, org_id: int, period: BillingPeriod | None = None) -> dict:
        """Get the current UTC usage month's cost summary with trends and breakdown."""
        period = period or billing_period()
        # Lazy sync: if no records exist for today, sync from adapters first
        today = period.today
        today_check = await session.execute(
            select(func.count())
            .select_from(CostRecord)
            .where(
                CostRecord.organization_id == org_id,
                CostRecord.record_date == today,
            )
        )
        if (today_check.scalar() or 0) == 0:
            # In its own session, and committed. A flush on the caller's session is
            # discarded when that session closes, so every other reader of
            # cost_records -- /history, and the dashboard's spend trend widget behind
            # it -- saw an empty table forever while this method re-synced and
            # re-discarded the same rows on every single call. It cannot commit on the
            # caller's session either: auto_run_service calls this mid-transaction and
            # that would commit its pending cancellations early.
            from app.database import async_session_factory

            async with async_session_factory() as sync_session:
                await CostService.sync_billing_data(sync_session, org_id, period)
                await sync_session.commit()

        # Current month total
        current_month_spend = await _usage_month_spend(session, org_id, period)

        # Daily trend
        daily_result = await session.execute(
            select(
                CostRecord.record_date,
                func.sum(CostRecord.cost_amount).label("amount"),
            )
            .where(*_in_usage_month(org_id, period))
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
            .where(*_in_usage_month(org_id, period))
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
        days_elapsed = period.days_elapsed
        days_in_month = period.days_in_month
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
        records: list[dict[str, Any]] = [{"date": str(row[0]), "amount": row[1]} for row in result.all()]
        total = sum(r["amount"] for r in records) if records else Decimal("0")
        return records, total

    @staticmethod
    async def sync_billing_data(
        session: AsyncSession, org_id: int, period: BillingPeriod | None = None
    ) -> UsageImport | None:
        """Sync cost data into cost_records.

        When BigQuery billing export is configured, imports provider actuals for
        the previous and current UTC usage months, excluding today (see
        ``_import_usage`` for the replacement rule), and uses adapter estimates
        for today only. When not configured, uses the adapter path for all data
        and returns None.

        Serialised per org by a transaction-scoped advisory lock. Every writer
        here is a select-then-insert and cost_records has no unique constraint,
        so two syncs racing on a day with no rows yet would both insert and
        double that day's spend. The lock releases when the caller commits.
        """
        await session.execute(select(func.pg_advisory_xact_lock(_COST_SYNC_LOCK_NAMESPACE, org_id)))
        logger.info("Syncing billing data for org %d", org_id)
        period = period or billing_period()

        bq_config = await _billing_export_config(session)
        if bq_config is None:
            await CostService._sync_from_adapters(session, org_id, period)
            return None

        # Historical data from the export (excludes today due to export lag)
        report = await _import_usage(session, org_id, bq_config, period.previous_month_start, period.today, period)
        # Today's costs from adapters (intraday bridge)
        await CostService._sync_from_adapters(session, org_id, period)
        return report

    @staticmethod
    async def backfill_billing_data(
        session: AsyncSession,
        org_id: int,
        start_date: date,
        end_date: date,
        period: BillingPeriod | None = None,
    ) -> UsageImport:
        """Re-import provider costs for usage days ``start_date..end_date`` (inclusive).

        The operator path for days outside the routine refresh window: the same
        query and replacement rule as ``sync_billing_data``, never reaching
        today. Days the provider has no actuals for keep their records and are
        listed in ``days_without_actuals``. Takes the same per-org lock; the
        caller commits.
        """
        from app.exceptions import ValidationError

        period = period or billing_period()
        if start_date > end_date:
            raise ValidationError("start_date must not be after end_date")
        usage_end = min(end_date + timedelta(days=1), period.today)
        if start_date >= usage_end:
            raise ValidationError(f"a backfill must end before today ({period.today.isoformat()}, UTC)")

        await session.execute(select(func.pg_advisory_xact_lock(_COST_SYNC_LOCK_NAMESPACE, org_id)))
        bq_config = await _billing_export_config(session)
        if bq_config is None:
            raise ValidationError("billing export is not configured")
        return await _import_usage(session, org_id, bq_config, start_date, usage_end, period)

    @staticmethod
    async def _sync_from_adapters(
        session: AsyncSession,
        org_id: int,
        period: BillingPeriod,
    ) -> None:
        """Sync today's cost data from infrastructure adapters."""
        today = period.today
        compute_adapter = get_compute_adapter()
        cluster_metrics = await compute_adapter.get_cluster_metrics()
        node_cost_daily = Decimal("0")
        compute_cost_daily = Decimal("0")

        for pool in cluster_metrics.node_pools:
            hourly = Decimal(str(pool.cost_rate_hourly or 0))
            if pool.name == "bioaf-platform":
                node_cost_daily += hourly * 24
            else:
                compute_cost_daily += hourly * 24

        storage_adapter = get_storage_adapter()
        storage_metrics = await storage_adapter.get_storage_metrics()
        storage_cost_monthly = Decimal(str(storage_metrics.total_cost_monthly_usd))
        storage_cost_daily = storage_cost_monthly / period.days_in_month

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
                record.metadata_json = {"source": "estimated"}
            else:
                session.add(
                    CostRecord(
                        organization_id=org_id,
                        record_date=today,
                        component=component,
                        cost_amount=amount,
                        metadata_json={"source": "estimated"},
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
    async def check_budget_thresholds(session: AsyncSession, org_id: int, period: BillingPeriod | None = None) -> None:
        """Compare the UTC usage month's spend to budget, emit events for crossed thresholds."""
        config = await CostService.get_budget_config(session, org_id)
        if not config or not config.monthly_budget:
            return

        current_spend = await _usage_month_spend(session, org_id, period or billing_period())
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
