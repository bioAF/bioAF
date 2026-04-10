"""Budget pre-flight engine for pipeline cost estimation and budget checking.

Estimates pipeline costs based on historical data, checks against monthly
budgets, and provides budget status for decision-making.
"""

import os
from decimal import Decimal
from statistics import mean, stdev

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.models.pipeline_cost_history import PipelineCostHistory
from app.models.pipeline_run import PipelineRun


class BudgetCheckResult(BaseModel):
    estimated_cost: float
    confidence_interval_pct: float
    current_month_spend: float
    queued_running_cost: float
    projected_total: float
    monthly_budget: float
    decision: str


# Default values for local/POC mode
DEFAULT_PIPELINE_COST = Decimal("5.00")
DEFAULT_MONTHLY_BUDGET = Decimal("500.00")
DEFAULT_CONFIDENCE_INTERVAL = 50.0  # Wide interval when no history


class BudgetService:
    @staticmethod
    async def estimate_pipeline_cost(
        pipeline_name: str,
        input_file_count: int,
        input_total_bytes: int,
        db: AsyncSession,
    ) -> tuple[float, float, int]:
        """Estimate cost for a pipeline run based on historical data.

        Returns (estimated_cost, confidence_interval_pct, history_count).
        """
        # Query similar runs (+/- 50% file count)
        min_files = max(1, int(input_file_count * 0.5))
        max_files = int(input_file_count * 1.5)

        result = await db.execute(
            select(PipelineCostHistory.actual_cost)
            .where(
                PipelineCostHistory.pipeline_name == pipeline_name,
                PipelineCostHistory.actual_cost.isnot(None),
                PipelineCostHistory.input_file_count >= min_files,
                PipelineCostHistory.input_file_count <= max_files,
            )
            .order_by(PipelineCostHistory.created_at.desc())
            .limit(10)
        )
        costs = [float(row[0]) for row in result.fetchall()]

        if not costs:
            return float(DEFAULT_PIPELINE_COST), DEFAULT_CONFIDENCE_INTERVAL, 0

        avg = mean(costs)
        if len(costs) >= 2:
            sd = stdev(costs)
            ci_pct = (sd / avg * 100) if avg > 0 else 15.0
            ci_pct = min(ci_pct, 50.0)  # Cap at 50%
        else:
            ci_pct = 15.0  # Default when only 1 data point

        return avg, ci_pct, len(costs)

    @staticmethod
    async def get_current_spend(db: AsyncSession) -> float:
        """Get current month's spend. In local mode, returns a mock value."""
        mock_spend = os.environ.get("BIOAF_MOCK_MONTHLY_SPEND")
        if mock_spend:
            return float(mock_spend)
        # In production, this would query GCP Billing API
        # For local mode, sum up actual_cost from cost history this month
        result = await db.execute(
            select(func.coalesce(func.sum(PipelineCostHistory.actual_cost), 0)).where(
                func.extract("month", PipelineCostHistory.created_at) == func.extract("month", func.now()),
                func.extract("year", PipelineCostHistory.created_at) == func.extract("year", func.now()),
            )
        )
        return float(result.scalar_one())

    @staticmethod
    async def get_queued_running_cost(db: AsyncSession) -> float:
        """Sum estimated costs for all queued and running pipeline runs."""
        result = await db.execute(
            select(func.coalesce(func.sum(PipelineRun.cost_estimate), 0)).where(
                PipelineRun.status.in_(["pending", "queued", "running"])
            )
        )
        return float(result.scalar_one())

    @staticmethod
    async def get_monthly_budget(db: AsyncSession) -> float:
        """Get the monthly budget. Returns default for local mode."""
        mock_budget = os.environ.get("BIOAF_MONTHLY_BUDGET")
        if mock_budget:
            return float(mock_budget)
        return float(DEFAULT_MONTHLY_BUDGET)

    @staticmethod
    async def check_budget(
        estimated_cost: float,
        confidence_interval_pct: float,
        db: AsyncSession,
    ) -> BudgetCheckResult:
        """Check if a pipeline run fits within the monthly budget."""
        current_spend = await BudgetService.get_current_spend(db)
        queued_running_cost = await BudgetService.get_queued_running_cost(db)
        monthly_budget = await BudgetService.get_monthly_budget(db)

        projected_total = current_spend + queued_running_cost + estimated_cost
        margin = estimated_cost * (confidence_interval_pct / 100.0)

        if current_spend >= monthly_budget:
            decision = "budget_exhausted"
        elif projected_total + margin > monthly_budget:
            decision = "will_exceed"
        elif projected_total > monthly_budget - margin:
            decision = "might_exceed"
        else:
            decision = "within_budget"

        return BudgetCheckResult(
            estimated_cost=estimated_cost,
            confidence_interval_pct=confidence_interval_pct,
            current_month_spend=current_spend,
            queued_running_cost=queued_running_cost,
            projected_total=projected_total,
            monthly_budget=monthly_budget,
            decision=decision,
        )

    @staticmethod
    async def budget_preflight(
        pipeline_name: str,
        input_file_count: int,
        input_total_bytes: int,
        db: AsyncSession,
    ) -> BudgetCheckResult:
        """Full pre-flight: estimate cost + check budget."""
        estimated_cost, ci_pct, _ = await BudgetService.estimate_pipeline_cost(
            pipeline_name, input_file_count, input_total_bytes, db
        )
        return await BudgetService.check_budget(estimated_cost, ci_pct, db)

    @staticmethod
    async def record_cost_history(
        pipeline_run_id: int,
        pipeline_name: str,
        input_file_count: int,
        input_total_bytes: int,
        estimated_cost: Decimal,
        actual_cost: Decimal,
        db: AsyncSession,
    ) -> PipelineCostHistory:
        """Record cost data after pipeline completion."""
        error_pct = None
        if estimated_cost and estimated_cost > 0:
            error_pct = (actual_cost - estimated_cost) / estimated_cost * 100

        record = PipelineCostHistory(
            pipeline_run_id=pipeline_run_id,
            pipeline_name=pipeline_name,
            input_file_count=input_file_count,
            input_total_bytes=input_total_bytes,
            estimated_cost=estimated_cost,
            actual_cost=actual_cost,
            estimation_error_pct=error_pct,
        )
        db.add(record)
        await db.flush()
        return record

    @staticmethod
    async def get_estimation_accuracy(
        pipeline_name: str,
        db: AsyncSession,
    ) -> dict:
        """Return accuracy stats for the cost estimation dashboard."""
        result = await db.execute(
            select(PipelineCostHistory).where(
                PipelineCostHistory.pipeline_name == pipeline_name,
                PipelineCostHistory.estimation_error_pct.isnot(None),
            )
        )
        records = list(result.scalars().all())
        if not records:
            return {"pipeline_name": pipeline_name, "record_count": 0, "mean_error_pct": None, "std_error_pct": None}

        errors = [float(r.estimation_error_pct) for r in records]
        return {
            "pipeline_name": pipeline_name,
            "record_count": len(records),
            "mean_error_pct": mean(errors),
            "std_error_pct": stdev(errors) if len(errors) >= 2 else 0.0,
        }
