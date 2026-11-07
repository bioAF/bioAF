"""GCP BigQuery implementation of BillingProvider (Phase 9D, ADR-028)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from google.cloud import bigquery

from app.adapters.billing.base import BillingProvider, UnsupportedBillingExport

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

    async def query_usage_costs(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        usage_start: date,
        usage_end: date,
    ) -> list[dict]:
        # The export is partitioned by ingestion time. Rows for a usage day are
        # ingested on or after that day, so partitions before the window start
        # are pruned; there is no upper partition bound because late exports and
        # corrections for in-window usage land in later partitions.
        start = datetime(usage_start.year, usage_start.month, usage_start.day, tzinfo=timezone.utc)
        end = datetime(usage_end.year, usage_end.month, usage_end.day, tzinfo=timezone.utc)
        table_ref = f"{project_id}.{dataset_id}.{table_id}"

        query = f"""
            SELECT
                service.description AS service_name,
                SUM(cost) + SUM(IFNULL(
                    (SELECT SUM(c.amount) FROM UNNEST(credits) c), 0
                )) AS net_cost,
                DATE(usage_start_time) AS usage_date
            FROM `{table_ref}`
            WHERE _PARTITIONTIME >= @partition_start
              AND usage_start_time >= @usage_start
              AND usage_start_time < @usage_end
            GROUP BY service_name, usage_date
            ORDER BY usage_date, service_name
        """  # noqa: S608

        def _query() -> list[dict]:
            client = bigquery.Client(project=project_id, credentials=self.credentials)
            partitioning = client.get_table(table_ref).time_partitioning
            if partitioning is None or partitioning.field is not None:
                raise UnsupportedBillingExport(
                    f"{table_ref} is not partitioned by ingestion time; refusing an unbounded scan"
                )
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("partition_start", "TIMESTAMP", start),
                    bigquery.ScalarQueryParameter("usage_start", "TIMESTAMP", start),
                    bigquery.ScalarQueryParameter("usage_end", "TIMESTAMP", end),
                ]
            )
            job = client.query(query, job_config=job_config)
            # Materialized in full: an error partway through raises instead of
            # returning a truncated result.
            return [
                {
                    "service_name": row.service_name,
                    "net_cost": float(row.net_cost),
                    "usage_date": row.usage_date,
                }
                for row in job.result()
            ]

        return await asyncio.to_thread(_query)
