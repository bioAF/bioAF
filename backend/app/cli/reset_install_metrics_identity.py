"""Reset this installation's metrics identity: for a deliberate clone, never for a restore (ADR-075).

A restored backup keeps the Installation ID and choice it was taken with. A copy of an installation that
will keep running beside the original needs its own identity. This clears the Installation ID, the admin's
choice and every reserved report (queued payloads included); the next admin is asked again, and opting in
creates a new ID. Reports the original already sent are unaffected.

Usage (on the host):
    ./bioaf reset-install-metrics-identity --confirm
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

from app.services.install_metrics.state import InstallMetricsService


async def run(argv: list[str], factory: Callable) -> int:
    if argv != ["--confirm"]:
        print(
            "This clears the installation metrics ID, the saved choice and all queued reports.\n"
            "Run it only on a deliberate copy of an installation. Re-run with --confirm to proceed.",
            file=sys.stderr,
        )
        return 2
    async with factory() as session:
        await InstallMetricsService.reset_identity(session)
        await session.commit()
    print("Installation metrics identity reset: an admin will be asked to choose again.")
    return 0


def main() -> int:
    from app.database import async_session_factory

    try:
        return asyncio.run(run(sys.argv[1:], async_session_factory))
    except Exception:
        print("reset_install_metrics_identity: failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
