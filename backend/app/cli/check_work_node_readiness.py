"""Check whether an update may replace the running backend: ``python -m app.cli.check_work_node_readiness``.

``./bioaf update`` runs this from the target release's image in a one-off container (no server,
no database upgrade) before it pins or restarts anything, and again after closing Work Node launch
admission. See ``app.services.work_node_readiness``.

Exit status: 0 ready; 3 older Work Nodes may still be running (the update must wait); 1 the check
could not run or the provider inventory was unavailable (the update must not proceed). Output is a
fixed summary line; provider errors stay in the container's stderr, never in the update status.
"""

from __future__ import annotations

import asyncio
import sys

from app.services.work_node_readiness import InventoryUnavailable, Readiness

EX_READY = 0
EX_UNAVAILABLE = 1
EX_DEFERRED = 3


async def _check() -> Readiness:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import settings
    from app.settings_contract import is_simulated
    from app.services.work_node_readiness import assess_update_readiness

    if is_simulated(settings.compute_mode):
        # Simulated nodes have no VM and no key to lose.
        return Readiness(ready=True)

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:

            async def inventory():
                from app.adapters.registry import _create_work_node_adapter
                from app.platform.cloud_provider import backend_for, load_resolved_backends

                try:
                    await load_resolved_backends(session)
                    adapter = _create_work_node_adapter(backend_for("work_node"), session_factory=factory)
                    await adapter.load_config(force=True)
                except Exception as exc:
                    raise InventoryUnavailable(type(exc).__name__) from exc
                return await adapter.inventory_instances()

            return await assess_update_readiness(session, inventory)
    finally:
        await engine.dispose()


async def run() -> int:
    try:
        result = await _check()
    except InventoryUnavailable as exc:
        print(
            f"Work Node update readiness could not be checked: provider inventory unavailable ({exc}).", file=sys.stderr
        )
        return EX_UNAVAILABLE
    except Exception as exc:
        print(f"Work Node update readiness could not be checked ({type(exc).__name__}).", file=sys.stderr)
        return EX_UNAVAILABLE
    if result.ready:
        print("Work Node update readiness: ready.")
        return EX_READY
    print(f"Work Node update readiness: {result.unsafe_count} older work node(s) may still be running.")
    return EX_DEFERRED


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
