"""List, and with --apply contain, notebook sessions still behind a public listener (plan 03.5F).

    python -m app.cli.contain_notebook_exposure            # report only
    python -m app.cli.contain_notebook_exposure --apply    # remove public listeners

Containment changes each session's own Service to ClusterIP; no Pod, home or output is deleted and no
session is stopped. Sessions marked "save and relaunch" still hold the old Kubernetes credential:
their owners should Stop them (outputs are saved) and start a new session.
Exit status: 0 nothing exposed remains, 3 exposure remains (report only, or a session could not be
changed), 1 the check could not run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def run(apply: bool) -> int:
    from app.adapters.registry import get_notebook_adapter, initialize_adapters
    from app.database import async_session_factory
    from app.services.notebook_exposure import contain_exposed_sessions

    async with async_session_factory() as session:
        await initialize_adapters(session, session_factory=async_session_factory)
        findings = await contain_exposed_sessions(session, get_notebook_adapter(), apply=apply)
        await session.commit()
    remaining = 0
    for f in findings:
        state = "contained" if f.contained else ("EXPOSED" if f.exposed else ("unknown" if f.exposed is None else "ok"))
        if f.exposed and not f.contained or f.exposed is None:
            remaining += 1
        relaunch = "; save and relaunch" if f.relaunch_needed else ""
        print(f"session {f.session_id} (user {f.user_id}): {state}: {f.detail}{relaunch}")
    if not findings:
        print("No live notebook sessions.")
    if remaining:
        print(f"{remaining} session(s) still need attention." + ("" if apply else " Run with --apply to contain them."))
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="remove public listeners (no session is stopped)")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.apply))
    except Exception as exc:
        print(f"The notebook exposure check could not run ({type(exc).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
