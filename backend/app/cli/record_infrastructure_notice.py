"""Record whether this release's infrastructure definitions are waiting to be applied (mockup M17).

Run by ``./bioaf update`` inside the backend container after readiness and final host completion.
It compares the installed release's definition fingerprints with what was last reconciled and
records the pending notice for administrators. It runs no Terraform and calls no cloud. It does
not depend on installation metrics consent. A version that is not the running release is refused,
so a stale or mismatched completion cannot create a notice. Startup reconciles the same way, so a
missed call is caught up.

Usage:
    python -m app.cli.record_infrastructure_notice --to-version <v>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise SystemExit(2)


async def run(argv: list[str], factory: Callable, running_version: str) -> int:
    from app.services.infrastructure_definitions import reconcile_notice

    try:
        parser = _Parser(prog="record_infrastructure_notice", add_help=False)
        parser.add_argument("--to-version", required=True)
        args = parser.parse_args(argv)
    except SystemExit:
        print("record_infrastructure_notice: refused (arguments)", file=sys.stderr)
        return 2
    if args.to_version.lstrip("v") != running_version.lstrip("v"):
        print("record_infrastructure_notice: refused (version)", file=sys.stderr)
        return 2
    async with factory() as session:
        notice = await reconcile_notice(session, running_version)
        await session.commit()
    print(f"record_infrastructure_notice: {'pending' if notice else 'up_to_date'}")
    return 0


def main() -> int:
    from app.config import settings
    from app.database import async_session_factory

    try:
        return asyncio.run(run(sys.argv[1:], async_session_factory, settings.app_version))
    except Exception:
        print("record_infrastructure_notice: failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
