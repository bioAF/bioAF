"""BillingProvider: the BAL seam for historical cost data (Phase 9D).

A platform-service provider (distinct from the five runtime adapter categories in
app/adapters/base.py): it answers "what did this project cost, by service and
day". GCP queries the BigQuery billing export (ADR-028); AWS would query CUR /
Cost Explorer; on-prem may have no managed billing (capability-gated, the cost UI
hides). The provider returns raw per-service rows; the bioAF component taxonomy
mapping stays in the service layer so the provider holds no bioAF domain vocab.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date


class BillingProvider(ABC):
    """Read historical cost data from the backend's billing export."""

    @abstractmethod
    async def verify_dataset(self, project_id: str, dataset_id: str) -> dict:
        """Check whether a billing-export table exists in ``dataset_id``.

        Returns ``{"found": True, "table_id": "..."}`` or ``{"found": False}``.
        """

    @abstractmethod
    async def query_usage_costs(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        usage_start: date,
        usage_end: date,
    ) -> list[dict]:
        """Return cost rows for UTC usage days in ``[usage_start, usage_end)``.

        Each row: ``{"service_name": str, "net_cost": float, "usage_date": date}``,
        one per service and UTC usage day, summing every charge for that usage
        regardless of the invoice month it was billed in. The caller chooses the
        window (and so excludes today); the provider reads no clock. Raises if
        the result cannot be read completely, so a caller never acts on a
        partial result.
        """


class UnsupportedBillingExport(Exception):
    """The billing export cannot be queried with a bounded scan."""
