"""Approve custom Git server keys: ``python -m app.cli.import_git_known_hosts FILE --source TEXT``.

Plan 03C. An operator with host access (``./bioaf import-git-known-hosts FILE "SOURCE"``) supplies
known_hosts lines for a custom Git server, obtained and checked through a channel independent of
the connection itself (the server administrator, a vendor portal, a signed document). Never feed it
raw ``ssh-keyscan`` output: a scan proves nothing about who answered.

Each line must name exactly one host (``host`` or ``[host]:port``) with one supported key type.
The file replaces the previously approved set; nothing is stored if any line is invalid. Work Node,
RStudio and custom-pipeline startup then verify that host strictly.

Exit status: 0 stored; 2 the material was refused; 1 the command could not run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


async def run(path: str, *, source: str, engine=None) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.platform.git_trust import KnownHostsError, import_custom_known_hosts

    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"The file could not be read ({type(exc).__name__}).", file=sys.stderr)
        return 1
    owned = engine is None
    if owned:
        from app.config import settings

        engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                stored = await import_custom_known_hosts(session, text, source=source)
            except KnownHostsError as exc:
                print(f"Refused: {exc}. Nothing was changed.", file=sys.stderr)
                return 2
            await session.commit()
    finally:
        if owned:
            await engine.dispose()
    print(f"Approved {len(stored)} Git server key(s):")
    for entry in stored:
        port = "" if entry["port"] == 22 else f":{entry['port']}"
        print(f"  {entry['host']}{port}  {entry['fingerprint']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file")
    parser.add_argument("--source", required=True, help="where the keys came from and how they were checked")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.file, source=args.source))


if __name__ == "__main__":
    raise SystemExit(main())
