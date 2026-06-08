"""GCP BigQuery implementation of BillingProvider (Phase 9D, ADR-028)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from google.cloud import bigquery

from app.adapters.billing.base import BillingProvider

# Billing-export table name prefixes (standard usage cost + detailed/resource cost).
_EXPORT_TABLE_PREFIXES = ("gcp_billing_export_v1_", "gcp_billing_export_resource_v1_")


class GcpBillingProvider(BillingProvider):
    """Queries the BigQuery billing export for historical cost data."""

    def __init__(self, credentials=None):
        self.credentials = credentials

    async def verify_dataset(self, project_id: str, dataset_id: str) -> dict:
        def _verify() -> dict:
            client = bigquery.Client(project=project_id, credentials=self.credentials)
            for table in client.list_tables(f"{project_id}.{dataset_id}"):
                if table.table_id.startswith(_EXPORT_TABLE_PREFIXES):
                    return {"found": True, "table_id": table.table_id}
            return {"found": False}

        return await asyncio.to_thread(_verify)

    async def query_mtd_costs(self, project_id: str, dataset_id: str, table_id: str) -> list[dict]:
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
            client = bigquery.Client(project=project_id, credentials=self.credentials)
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("invoice_month", "STRING", invoice_month),
                ]
            )
            job = client.query(query, job_config=job_config)
            return [
                {
                    "service_name": row.service_name,
                    "net_cost": float(row.net_cost),
                    "usage_date": row.usage_date,
                }
                for row in job.result()
            ]

        return await asyncio.to_thread(_query)
