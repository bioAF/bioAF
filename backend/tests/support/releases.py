"""Which Alembic revision each released bioAF version installs (plan 9, section 5).

Every previously released install may still exist and may skip any number of releases, so the
historical upgrade matrix starts from each release's schema. This maps tags to that starting point
from local Git objects, never from a census of installs::

    python -m tests.support.releases --write    # regenerate tests/release_revisions.json

Each entry records the tag, its commit, the Alembic head it installs, and the Git tree hash of
``backend/alembic/versions`` (the migration-tree fingerprint). Releases with the same tree build the
same schema and share a basic upgrade case. A different tree with the same head is a distinct case
unless its difference is recorded as semantically equivalent (``EQUIVALENT_EDITS``).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MAPPING = Path(__file__).resolve().parent.parent / "release_revisions.json"
VERSIONS = "backend/alembic/versions"
RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+$")

# Published migration files edited after release (follow-up review, 2026-11-07). A different tree hash
# alone does not establish a different schema; these are the reasons the trees differ.
EQUIVALENT_EDITS = {
    "2ff2018a": "comment/docstring edits only",
    "912bb4bd": "comment/docstring edits; 076 helper import paths moved (same behavior)",
    "604f9aa8": "038 scopes a catalog query to current_schema(); same result in a dedicated database",
}


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True).stdout


def release_tags() -> list[str]:
    return [t for t in _git("tag", "--list", "v*", "--sort=v:refname").split() if RELEASE_TAG.match(t)]


def head_revision(ref: str) -> str:
    """The single Alembic head in ``ref``'s migrations, from revision/down_revision assignments."""
    revisions: dict[str, set[str]] = {}
    for name in _git("ls-tree", "--name-only", f"{ref}:{VERSIONS}").split():
        if not name.endswith(".py"):
            continue
        tree = ast.parse(_git("show", f"{ref}:{VERSIONS}/{name}"))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in {"revision", "down_revision"}:
                    values[node.targets[0].id] = ast.literal_eval(node.value)
        down = values.get("down_revision")
        revisions[values["revision"]] = set(down if isinstance(down, (tuple, list)) else [down] if down else [])
    parents = set().union(*revisions.values())
    heads = sorted(set(revisions) - parents)
    if len(heads) != 1:
        raise ValueError(f"{ref} has {len(heads)} Alembic heads: {heads}")
    return heads[0]


def build() -> list[dict]:
    out = []
    for tag in release_tags():
        out.append(
            {
                "tag": tag,
                "commit": _git("rev-parse", f"{tag}^{{commit}}").strip(),
                "head": head_revision(tag),
                "migration_tree": _git("rev-parse", f"{tag}:{VERSIONS}").strip(),
            }
        )
    return out


def load() -> list[dict]:
    return json.loads(MAPPING.read_text())["releases"]


def current() -> list[dict]:
    """The recorded mapping plus any release tagged since it was written (so the matrix never lags).

    A recorded entry must still match Git: a released tag, its commit and its migration tree never
    change. ``test_release_mapping_matches_git`` holds that.
    """
    recorded = load()
    known = {r["tag"] for r in recorded}
    extra = []
    for tag in release_tags():
        if tag not in known:
            extra.append(
                {
                    "tag": tag,
                    "commit": _git("rev-parse", f"{tag}^{{commit}}").strip(),
                    "head": head_revision(tag),
                    "migration_tree": _git("rev-parse", f"{tag}:{VERSIONS}").strip(),
                }
            )
    return recorded + extra


def recorded_against_git() -> list[str]:
    """Recorded entries Git no longer agrees with (empty when the record is true)."""
    problems = []
    tags = set(release_tags())
    for r in load():
        if r["tag"] not in tags:
            problems.append(f"{r['tag']} is recorded but not tagged in this clone")
            continue
        commit = _git("rev-parse", f"{r['tag']}^{{commit}}").strip()
        tree = _git("rev-parse", f"{r['tag']}:{VERSIONS}").strip()
        if (commit, tree) != (r["commit"], r["migration_tree"]):
            problems.append(f"{r['tag']} moved: {commit[:10]} {tree[:10]}")
    return problems


def distinct_trees(releases: list[dict]) -> list[dict]:
    """One release per migration tree (the earliest), the basic-case starting points."""
    seen: dict[str, dict] = {}
    for r in releases:
        seen.setdefault(r["migration_tree"], r)
    return list(seen.values())


def main(argv: list[str]) -> int:
    releases = build()
    payload = {
        "_about": "Release tag -> installed Alembic head and migration-tree fingerprint. "
        "Regenerate with `python -m tests.support.releases --write`.",
        "equivalent_edits": EQUIVALENT_EDITS,
        "releases": releases,
    }
    text = json.dumps(payload, indent=1) + "\n"
    if "--write" in argv:
        MAPPING.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
