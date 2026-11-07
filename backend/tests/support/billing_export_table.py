"""A controlled BigQuery billing-export table for provider and import tests.

Stands in for ``google.cloud.bigquery.Client`` in app/adapters/billing/gcp.py.
It holds raw export rows (usage time, invoice month, ingestion time, cost,
credits) and answers a query by applying the predicates the submitted SQL
binds, then grouping by service and UTC usage day as the real query does. It
records every (sql, params) pair so a test can assert the query-cost contract.

Each predicate is applied only when the SQL states it, so a query that drops a
filter returns the rows that filter would have excluded:

    ``invoice.month = @invoice_month``           row.invoice_month == param
    ``_PARTITIONTIME >= @partition_start``       ingestion partition start >= param
    ``usage_start_time >= @usage_start``         row.usage_start_time >= param
    ``usage_start_time < @usage_end``            row.usage_start_time < param
    ``DATE(usage_start_time) < CURRENT_DATE()``  usage day < today (UTC wall clock)

``fail_with`` alone makes the query raise when submitted; with ``fail_after``
the result iterator yields that many rows and then raises, to model a query
that fails partway through being consumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import SimpleNamespace


@dataclass
class ExportRow:
    service: str
    cost: float
    usage_start_time: datetime
    invoice_month: str
    export_time: datetime
    credits: list[float] = field(default_factory=list)


_GRANULARITY = {"ingestion_hour": "HOUR", "ingestion_day": "DAY", "ingestion_month": "MONTH", "ingestion_year": "YEAR"}


def _partition_time(ts: datetime, granularity: str) -> datetime:
    """_PARTITIONTIME for a row ingested at ``ts``: the start of its UTC partition."""
    ts = ts.astimezone(timezone.utc)
    if granularity == "HOUR":
        return datetime(ts.year, ts.month, ts.day, ts.hour, tzinfo=timezone.utc)
    if granularity == "MONTH":
        return datetime(ts.year, ts.month, 1, tzinfo=timezone.utc)
    if granularity == "YEAR":
        return datetime(ts.year, 1, 1, tzinfo=timezone.utc)
    return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)


class BillingExportTable:
    def __init__(self, rows: list[ExportRow] | None = None, partitioning: str = "ingestion_day") -> None:
        self.rows = list(rows or [])
        # "ingestion_hour" / "_day" / "_month" / "_year", "column" (partitioned on a column), or "none".
        self.partitioning = partitioning
        self.queries: list[tuple[str, dict]] = []
        self.fail_after: int | None = None
        self.fail_with: BaseException | None = None

    # -- google.cloud.bigquery.Client surface --------------------------------------

    def client(self, project=None, credentials=None) -> BillingExportTable:
        return self

    def get_table(self, ref):
        if self.partitioning == "none":
            tp = None
        elif self.partitioning == "column":
            tp = SimpleNamespace(type_="DAY", field="usage_start_time")
        else:
            tp = SimpleNamespace(type_=_GRANULARITY[self.partitioning], field=None)
        return SimpleNamespace(time_partitioning=tp)

    def query(self, sql: str, job_config=None):
        params = {p.name: p.value for p in (job_config.query_parameters if job_config else [])}
        self.queries.append((sql, params))
        if self.fail_with is not None and self.fail_after is None:
            raise self.fail_with
        normalized = " ".join(sql.split())
        selected = [r for r in self.rows if self._matches(r, normalized, params)]

        grouped: dict[tuple[str, date], float] = {}
        for r in selected:
            key = (r.service, r.usage_start_time.astimezone(timezone.utc).date())
            grouped[key] = grouped.get(key, 0.0) + r.cost + sum(r.credits)
        result_rows = [
            SimpleNamespace(service_name=service, net_cost=amount, usage_date=usage_date)
            for (service, usage_date), amount in sorted(grouped.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        ]
        return SimpleNamespace(result=lambda: self._iterate(result_rows))

    # -- internals -------------------------------------------------------------------

    def _iterate(self, rows):
        for i, row in enumerate(rows):
            if self.fail_after is not None and i >= self.fail_after:
                break
            yield row
        if self.fail_after is not None:
            raise self.fail_with or RuntimeError("query failed while reading results")

    def _matches(self, r: ExportRow, sql: str, params: dict) -> bool:
        if "invoice.month = @invoice_month" in sql and r.invoice_month != params["invoice_month"]:
            return False
        if "_PARTITIONTIME >= @partition_start" in sql and (
            _partition_time(r.export_time, _GRANULARITY.get(self.partitioning, "DAY")) < params["partition_start"]
        ):
            return False
        if "usage_start_time >= @usage_start" in sql and r.usage_start_time < params["usage_start"]:
            return False
        if "usage_start_time < @usage_end" in sql and r.usage_start_time >= params["usage_end"]:
            return False
        if "DATE(usage_start_time) < CURRENT_DATE()" in sql:
            if r.usage_start_time.astimezone(timezone.utc).date() >= datetime.now(timezone.utc).date():
                return False
        return True
