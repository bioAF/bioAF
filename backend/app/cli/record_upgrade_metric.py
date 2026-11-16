"""Reserve the installation metrics report for one completed host upgrade (ADR-075).

Run by ``./bioaf update`` inside the backend container after readiness and final host completion. It
reserves the report locally, and only if the admin opted in before the operation started; it never
touches the network (the backend's sender delivers). Output is a fixed outcome code, never an argument.

Usage:
    python -m app.cli.record_upgrade_metric --operation-id <uuid> --from-version <v> --to-version <v> \\
        --started-at <iso> --completed-at <iso>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from datetime import datetime

from app.services.install_metrics import upgrades


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        # argparse would echo the offending value; report a fixed message instead.
        raise SystemExit(2)


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = _Parser(prog="record_upgrade_metric", add_help=False)
    for name in ("--operation-id", "--from-version", "--to-version", "--started-at", "--completed-at"):
        parser.add_argument(name, required=True)
    return parser.parse_args(argv)


async def run(argv: list[str], factory: Callable, now: datetime | None = None) -> int:
    try:
        args = _arguments(argv)
        operation = upgrades.UpgradeOperation.parse(
            operation_id=args.operation_id,
            from_version=args.from_version,
            to_version=args.to_version,
            started_at=args.started_at,
            completed_at=args.completed_at,
        )
    except upgrades.InvalidOperation as field:
        print(f"record_upgrade_metric: refused ({field})", file=sys.stderr)
        return 2
    except SystemExit:
        print("record_upgrade_metric: refused (arguments)", file=sys.stderr)
        return 2
    async with factory() as session:
        outcome = await upgrades.record(session, operation, now)
        await session.commit()
    print(f"record_upgrade_metric: {outcome}")
    return 0


def main() -> int:
    from app.database import async_session_factory

    try:
        return asyncio.run(run(sys.argv[1:], async_session_factory))
    except Exception:
        print("record_upgrade_metric: failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
