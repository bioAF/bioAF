"""BigQuery billing export service (ADR-028).

Provides verification of billing export setup and querying of MTD cost data
from the GCP BigQuery billing export table.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from google.cloud import bigquery

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

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

        def _verify() -> dict:
            client = bigquery.Client(project=project_id, credentials=credentials)
            tables = list(client.list_tables(f"{project_id}.{dataset_id}"))
            for table in tables:
                if table.table_id.startswith("gcp_billing_export_v1_"):
                    return {"found": True, "table_id": table.table_id}
            return {"found": False}

        return await asyncio.to_thread(_verify)

    @staticmethod
    async def query_mtd_costs(
        project_id: str,
        dataset_id: str,
        table_id: str,
        credentials: Credentials | None = None,
    ) -> list[dict]:
        """Query month-to-date costs from the BQ billing export table.

        Returns a list of dicts with keys: service_name, component, net_cost, usage_date.
        Excludes today's data (it may be incomplete due to BQ export lag).
        """
        now = datetime.now(timezone.utc)
        invoice_month = now.strftime("%Y%m")

        query = f"""
            SELECT
                service.description AS service_name,
                SUM(cost) + SUM(IFNULL(
                    (SELECT SUM(c.amount) FROM UNNEST(credits) c), 0
                )) AS net_cost,
                DATE(usage_start_time) AS usage_date
            FROM `{project_id}.{dataset_id}.{table_id}`
            WHERE invoice.month = @invoice_month
              AND DATE(usage_start_time) < CURRENT_DATE()
            GROUP BY service_name, usage_date
            ORDER BY usage_date, service_name
        """  # noqa: S608

        def _query() -> list[dict]:
            client = bigquery.Client(project=project_id, credentials=credentials)
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("invoice_month", "STRING", invoice_month),
                ]
            )
            job = client.query(query, job_config=job_config)
            rows = job.result()
            results = []
            for row in rows:
                results.append(
                    {
                        "service_name": row.service_name,
                        "component": BillingExportService.map_service_to_component(row.service_name),
                        "net_cost": float(row.net_cost),
                        "usage_date": row.usage_date,
                    }
                )
            return results

        return await asyncio.to_thread(_query)
