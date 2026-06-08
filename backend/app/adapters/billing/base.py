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


class BillingProvider(ABC):
    """Read historical cost data from the backend's billing export."""

    @abstractmethod
    async def verify_dataset(self, project_id: str, dataset_id: str) -> dict:
        """Check whether a billing-export table exists in ``dataset_id``.

        Returns ``{"found": True, "table_id": "..."}`` or ``{"found": False}``.
        """

    @abstractmethod
    async def query_mtd_costs(self, project_id: str, dataset_id: str, table_id: str) -> list[dict]:
        """Return month-to-date cost rows, excluding today (export lag).

        Each row: ``{"service_name": str, "net_cost": float, "usage_date": date}``.
        """
