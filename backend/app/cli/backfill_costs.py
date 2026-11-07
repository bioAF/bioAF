"""CLI script to re-import provider costs for older usage days.

Routine billing sync refreshes only the previous and current UTC usage months.
Use this for corrections or repair outside that window. It runs the same import
as the sync: days two or more UTC days old that the provider has actuals for
are rebuilt from the provider's daily aggregate; days without provider actuals
keep their records and are listed as not reconstructable. Today is never
imported. Dates are UTC usage dates, inclusive.

Usage:
    python -m app.cli.backfill_costs --org-id 1 --start 2026-08-01 --end 2026-08-31
"""

import argparse
import asyncio
import os
import sys
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.exceptions import ValidationError
from app.services.cost_service import CostService, UsageImport


async def backfill_costs(session: AsyncSession, org_id: int, start_date: date, end_date: date) -> UsageImport:
    """Run the backfill for one org and commit it, or roll back if the import failed."""
    report = await CostService.backfill_billing_data(session, org_id, start_date, end_date)
    if report.status == "imported":
        await session.commit()
    else:
        await session.rollback()
    return report


def format_report(report: UsageImport) -> str:
    window = f"{report.usage_start.isoformat()} to {report.usage_end.isoformat()} (end exclusive)"
    if report.status != "imported":
        return f"Backfill {report.status} for {window}; no cost records were changed."
    lines = [
        f"Backfill of {window}: rebuilt {len(report.rebuilt_days)} day(s), "
        f"provisionally updated {len(report.provisional_days)} day(s).",
    ]
    if report.days_without_actuals:
        days = ", ".join(d.isoformat() for d in report.days_without_actuals)
        lines.append(f"No provider actuals, records kept (not reconstructable): {days}")
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> None:
    database_url = os.environ.get("BIOAF_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: Set BIOAF_DATABASE_URL or DATABASE_URL environment variable.")
        sys.exit(1)

    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            report = await backfill_costs(session, args.org_id, args.start, args.end)
    except ValidationError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    finally:
        await engine.dispose()
    print(format_report(report))
    if report.status != "imported":
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-import provider costs for older UTC usage days")
    parser.add_argument("--org-id", type=int, required=True, help="Organization whose cost records to rebuild")
    parser.add_argument("--start", type=date.fromisoformat, required=True, help="First usage date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="Last usage date (YYYY-MM-DD)")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
