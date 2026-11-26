"""Release maintenance: compare the shipped GitHub host keys with GitHub's published metadata.

``python -m app.cli.check_github_known_hosts`` reads https://api.github.com/meta over verified TLS
and compares its ``ssh_keys`` with ``app/platform/git_known_hosts/github.com``. It is run when the
release asset is maintained, never at startup. A difference is applied by a reviewed change to the
shipped file (with overlap during a GitHub rotation), never by accepting a changed key.

Exit status: 0 identical; 1 different (the differences are printed); 2 the metadata was unavailable.
"""

from __future__ import annotations

import json
import sys
import urllib.request

META_URL = "https://api.github.com/meta"


def shipped_keys() -> list[str]:
    from app.platform.git_trust import github_known_hosts

    return [line.split(" ", 1)[1] for line in github_known_hosts().splitlines()]


def compare(published: list[str]) -> int:
    shipped, live = set(shipped_keys()), {k.strip() for k in published}
    if shipped == live:
        print(f"The shipped GitHub host keys match GitHub's published metadata ({len(live)} keys).")
        return 0
    for key in sorted(live - shipped):
        print(f"Published by GitHub, not shipped: {key.split()[0]} ...{key[-16:]}")
    for key in sorted(shipped - live):
        print(f"Shipped, no longer published: {key.split()[0]} ...{key[-16:]}")
    return 1


def main() -> int:
    request = urllib.request.Request(
        META_URL, headers={"Accept": "application/json", "User-Agent": "bioaf-maintenance"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (fixed https URL)
            published = json.load(response)["ssh_keys"]
    except Exception as exc:
        print(f"GitHub metadata unavailable ({type(exc).__name__}).", file=sys.stderr)
        return 2
    return compare(published)


if __name__ == "__main__":
    raise SystemExit(main())
