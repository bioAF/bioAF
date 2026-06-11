"""Locks the Literature router's public contract across the package split.

Phase 2 of the Literature cleanup splits the single 1.5k-line router module into
an ``app.api.literature`` package of sub-routers (papers, comments, sources, ...)
aggregated under the same ``/api/literature`` prefix. That split must not change
the HTTP surface at all.

``test_route_table_matches_golden`` is the guarantee: it compares the live
router against a golden snapshot captured from the pre-split module
(``tests/data/literature_route_table.golden.json``). Path, methods, endpoint
name, response model, status code, and tags must all match for every route, so
existing and new installs see a byte-for-byte identical API. The structural
tests assert the decomposition actually happened (and cannot silently collapse
back into one God module).
"""

from __future__ import annotations

import ast
import json
import typing
from pathlib import Path

import app.api.literature as lit_pkg
from app.api.literature import router

GOLDEN_PATH = Path(__file__).resolve().parent / "data" / "literature_route_table.golden.json"


def _model_repr(rm) -> str | None:
    """Render a response_model so that ``list[A]`` and ``list[B]`` differ.

    A plain ``__name__`` collapses every ``list[...]`` to ``"list"``, which would
    hide a swapped inner type. Keep the generic's argument names.
    """
    if rm is None:
        return None
    if isinstance(rm, type):
        return rm.__name__
    origin = typing.get_origin(rm)
    args = typing.get_args(rm)
    if origin is not None and args:
        inner = ", ".join(a.__name__ if isinstance(a, type) else str(a) for a in args)
        return f"{getattr(origin, '__name__', str(origin))}[{inner}]"
    return str(rm)


def _live_route_table() -> list[dict]:
    rows = []
    for r in router.routes:
        methods = sorted(r.methods) if getattr(r, "methods", None) else []
        rows.append(
            {
                "path": r.path,
                "methods": methods,
                "name": r.name,
                "response_model": _model_repr(getattr(r, "response_model", None)),
                "status_code": getattr(r, "status_code", None),
                "tags": list(getattr(r, "tags", []) or []),
            }
        )
    rows.sort(key=lambda x: (x["path"], ",".join(x["methods"])))
    return rows


def test_route_table_matches_golden():
    """The live router exposes exactly the pre-split set of routes, unchanged."""
    golden = json.loads(GOLDEN_PATH.read_text())
    live = _live_route_table()

    # Catch an accidental duplicate registration: the dict comparison below
    # dedups by (path, methods), so a route registered twice would otherwise
    # slip through.
    assert len(router.routes) == len(golden), (
        f"route count changed: live has {len(router.routes)} routes, golden has {len(golden)}"
    )

    # Compare path-by-path so a mismatch points at the offending route.
    golden_by_key = {(r["path"], tuple(r["methods"])): r for r in golden}
    live_by_key = {(r["path"], tuple(r["methods"])): r for r in live}
    assert set(live_by_key) == set(golden_by_key), (
        "route set changed: "
        f"added={sorted(set(live_by_key) - set(golden_by_key))} "
        f"removed={sorted(set(golden_by_key) - set(live_by_key))}"
    )
    for key, expected in golden_by_key.items():
        assert live_by_key[key] == expected, f"route {key} changed: {live_by_key[key]} != {expected}"


def test_literature_is_a_package():
    """app.api.literature is a package, not a single module."""
    assert hasattr(lit_pkg, "__path__"), "app.api.literature should be a package"


def test_router_assembled_from_multiple_modules():
    """The router is composed from many sub-router modules, not one God module."""
    modules = {r.endpoint.__module__ for r in router.routes if hasattr(r, "endpoint")}
    assert len(modules) >= 5, f"expected the router to span several sub-modules, got {sorted(modules)}"


def test_no_inline_basemodels_anywhere_in_package():
    """No module in the package declares a Pydantic schema inline.

    Extends the schema-layering guarantee (test_literature_api_schemas) to every
    file in the new package, so a sub-router cannot reintroduce inline schemas.
    """
    package_dir = Path(lit_pkg.__file__).resolve().parent
    offenders: dict[str, list[str]] = {}
    for py in sorted(package_dir.rglob("*.py")):
        tree = ast.parse(py.read_text())
        inline = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(isinstance(base, ast.Name) and base.id == "BaseModel" for base in node.bases)
        ]
        if inline:
            offenders[py.name] = inline
    assert not offenders, f"package modules define schemas inline: {offenders}"
