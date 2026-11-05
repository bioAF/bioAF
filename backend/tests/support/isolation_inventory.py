"""Classify backend test files by what they need from the database (plan 4, stage 4).

``python -m tests.support.isolation_inventory --write`` regenerates
``tests/isolation_inventory.json``; review the diff like code. The classifier
reads each file's AST: the fixtures its tests request (followed through the
fixtures defined in conftest and in the file), and signals that a test reads
across sessions, runs background work, or changes the schema.

A file's category picks its fixture route. Files the inventory does not list,
and ``unclassified`` files, keep the existing isolated path.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROUTES = {
    "schema": "dedicated_schema",
    "real_commit": "committed_reset_candidate",
    "single_connection": "rollback_candidate",
    "no_database": "none",
    "unclassified": "existing_isolated",
}

DB_FIXTURES = {"db_engine", "session", "client"}

# Files the plan names as needing real commits whatever their signals say.
SEEDS: dict[str, str] = {
    "test_pipeline_launch_failure.py": "pipeline launch outcomes and isolated quota-denial audits",
    "test_launch_outcome.py": "pipeline launch outcomes",
    "test_auto_run_launch_outcome.py": "pipeline launch outcomes",
    "test_launch_partial_submission.py": "pipeline launch outcomes",
    "test_audit.py": "isolated quota-denial audits",
    "test_post_commit_events.py": "post-commit events: outer commit versus savepoint",
    "test_api_keys.py": "API-key middleware reads keys through its own session",
    "test_terraform_routes_interruption.py": "Terraform interruption: background drains read committed state",
    "test_terraform_interruption.py": "Terraform interruption",
}

# Names whose presence means a test reaches the database outside its one session.
REAL_COMMIT_NAMES = {
    "async_session_factory",
    "async_sessionmaker",
    "wait_for_deferred_events",
    "defer_event",
    "create_task",
    "BackgroundTasks",
    "add_task",
}

_DDL = re.compile(r"\b(CREATE|ALTER|DROP)\s+(TABLE|SCHEMA|TYPE|INDEX)\b", re.IGNORECASE)
_SCHEMA_NAMES = {"alembic", "migrated_schema", "create_all", "drop_all"}


@dataclass
class Entry:
    category: str
    evidence: list[str] = field(default_factory=list)

    @property
    def route(self) -> str:
        return ROUTES[self.category]


def _fixture_params(tree: ast.Module) -> dict[str, set[str]]:
    """Fixture name -> the fixtures it requests, for fixtures defined at module level."""
    graph: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any("fixture" in ast.unparse(d) for d in node.decorator_list):
            graph[node.name] = {a.arg for a in node.args.args}
    return graph


def fixture_graph(conftests: list[Path]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in conftests:
        graph.update(_fixture_params(ast.parse(path.read_text())))
    return graph


def _closure(names: set[str], graph: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(names)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        stack.extend(graph.get(name, ()))
    return seen


def _requested(tree: ast.Module) -> set[str]:
    requested: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            requested.update(a.arg for a in node.args.args)
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    requested.update(a.arg for a in item.args.args if a.arg != "self")
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("usefixtures"):
            requested.update(a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
    # Autouse fixtures in the file count as requested.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            "autouse=True" in ast.unparse(d) for d in node.decorator_list
        ):
            requested.add(node.name)
    return requested


def _names(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.update(node.name.split("."))
            if node.asname:
                found.add(node.asname)
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and isinstance(node, ast.ImportFrom) and node.module:
            found.update(node.module.split("."))
    return found


def classify(path: Path, graph: dict[str, set[str]], seeds: dict[str, str] | None = None) -> Entry:
    seeds = SEEDS if seeds is None else seeds
    source = path.read_text()
    tree = ast.parse(source)
    local_graph = {**graph, **_fixture_params(tree)}
    fixtures = _closure(_requested(tree), local_graph)
    names = _names(tree)
    db = sorted(fixtures & DB_FIXTURES)
    evidence = [f"fixture:{f}" for f in db]

    schema_signals = sorted(names & _SCHEMA_NAMES)
    if _DDL.search(source):
        schema_signals.append("ddl")
    if schema_signals:
        return Entry("schema", evidence + schema_signals)

    if "getfixturevalue" in names:
        return Entry("unclassified", evidence + ["getfixturevalue"])

    real = sorted(names & REAL_COMMIT_NAMES)
    if "client" in db:
        real.append("http requests use their own sessions")
    if "db_engine" in _requested_directly(tree, local_graph):
        real.append("uses db_engine directly")
    if path.name in seeds:
        real.append(f"seed: {seeds[path.name]}")
    if real:
        return Entry("real_commit", evidence + real)
    if db:
        return Entry("single_connection", evidence)
    return Entry("no_database", evidence)


def _requested_directly(tree: ast.Module, graph: dict[str, set[str]]) -> set[str]:
    """Fixtures requested by tests or by fixtures defined in this file (not via conftest's own chain)."""
    direct = _requested(tree)
    for name in _fixture_params(tree):
        direct |= graph.get(name, set())
    return direct


def build(tests_dir: Path) -> dict[str, Entry]:
    graph = fixture_graph([tests_dir / "conftest.py"])
    return {path.name: classify(path, graph) for path in sorted(tests_dir.glob("test_*.py"))}


def main(argv: list[str]) -> int:
    tests_dir = Path(__file__).resolve().parent.parent
    entries = build(tests_dir)
    payload = {
        "about": "Generated by `python -m tests.support.isolation_inventory --write`; see that module.",
        "routes": ROUTES,
        "files": {
            name: {"category": e.category, "route": e.route, "evidence": e.evidence} for name, e in entries.items()
        },
    }
    counts: dict[str, int] = {}
    for e in entries.values():
        counts[e.category] = counts.get(e.category, 0) + 1
    if "--write" in argv:
        (tests_dir / "isolation_inventory.json").write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
