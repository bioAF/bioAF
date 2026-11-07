"""BigQuery billing export service (ADR-028).

Provides verification of billing export setup and querying of usage-window cost data.
The backend's billing store is reached through the BAL ``BillingProvider``
(Phase 9D); this service owns the bioAF component taxonomy mapping and the
verify/query orchestration, holding no cloud SDK.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.adapters.billing import create_billing_provider
from app.platform.cloud_provider import backend_for

# Credentials are an opaque handle from the Credentials seam, passed straight
# through to the billing provider; the service names no cloud credential type.
Credentials = Any

logger = logging.getLogger("bioaf.billing_export_service")

# Map GCP service description to bioAF cost component
_SERVICE_COMPONENT_MAP: dict[str, str] = {
    "Compute Engine": "compute",
    "Cloud Storage": "storage",
    "Kubernetes Engine": "node",
}


class BillingExportService:
    @staticmethod
    def map_service_to_component(service_name: str) -> str:
        """Map a GCP service name to a bioAF cost component."""
        return _SERVICE_COMPONENT_MAP.get(service_name, "other")

    @staticmethod
    async def verify_dataset(
        project_id: str,
        dataset_id: str,
        credentials: Credentials | None = None,
    ) -> dict:
        """Check if the billing export table exists in the given dataset.

        Returns {"found": True, "table_id": "..."} or {"found": False}.
        """
        provider = create_billing_provider(credentials=credentials, backend=backend_for("billing"))
        return await provider.verify_dataset(project_id, dataset_id)

    @staticmethod
    async def query_usage_costs(
        project_id: str,
        dataset_id: str,
        table_id: str,
        usage_start: date,
        usage_end: date,
        credentials: Credentials | None = None,
    ) -> list[dict]:
        """Query costs for UTC usage days in ``[usage_start, usage_end)``.

        Returns a list of dicts with keys: service_name, component, net_cost, usage_date.
        Each row is a service's complete cost for one usage day across invoice
        months. The provider returns raw per-service rows; this maps each to a
        bioAF component.
        """
        provider = create_billing_provider(credentials=credentials, backend=backend_for("billing"))
        rows = await provider.query_usage_costs(project_id, dataset_id, table_id, usage_start, usage_end)
        return [
            {
                "service_name": row["service_name"],
                "component": BillingExportService.map_service_to_component(row["service_name"]),
                "net_cost": row["net_cost"],
                "usage_date": row["usage_date"],
            }
            for row in rows
        ]
