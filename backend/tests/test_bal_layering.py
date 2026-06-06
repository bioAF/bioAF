"""BAL import guardrail (BAL rework, Phase 0).

The governing rule of the BioAF Adapter Layer: no module outside
``backend/app/adapters/`` may import a cloud or Kubernetes SDK, and no module
inside ``backend/app/adapters/`` may import from ``backend/app/services/`` (the
layering inversion). ``install-gcp.sh`` is the one sanctioned exception and lives
outside ``backend/app``, so it is not scanned here.

This phase does not fix any leak. It freezes the current leak set in two
allowlists and fails the build when a new leak appears, or when an allowlisted
leak is removed but its allowlist entry is left behind (stale entry). The
allowlists therefore only ever shrink, phase by phase, until they are empty.

Detection is a static AST scan of import statements, not a runtime import, so it
catches imports at any nesting depth (module level, inside functions, inside
``TYPE_CHECKING`` blocks). Dynamic, string-based imports
(``importlib.import_module("google.cloud.storage")``) are a known blind spot.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Top-level packages no non-adapter module may import. boto3/botocore are
# forbidden pre-emptively: there is no AWS code yet, so it costs nothing now and
# prevents the leak from ever taking root.
FORBIDDEN_SDK_PREFIXES = (
    "google.cloud",
    "kubernetes",
    "boto3",
    "botocore",
)


def _forbidden_sdk_imports_in_source(source: str) -> set[str]:
    """Return the precise forbidden dotted paths imported by ``source``.

    ``import google.cloud.storage`` -> ``{"google.cloud.storage"}``.
    ``from google.cloud import storage`` -> ``{"google.cloud.storage"}`` (the
    imported name is a submodule of the bare prefix, so it is appended).
    ``from kubernetes import client`` -> ``{"kubernetes.client"}``.
    ``from google.cloud.storage import Blob`` -> ``{"google.cloud.storage"}``
    (the module itself already extends past the prefix).
    """
    found: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module = node.module
            if not _is_forbidden(module):
                continue
            if module in FORBIDDEN_SDK_PREFIXES:
                # `from google.cloud import storage` / `from kubernetes import client`
                for alias in node.names:
                    found.add(f"{module}.{alias.name}")
            else:
                # `from google.cloud.storage import Blob` -- module is the leak.
                found.add(module)
    return found


def _is_forbidden(name: str) -> bool:
    return any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in FORBIDDEN_SDK_PREFIXES
    )


# --- Detector unit tests (no real files; prove the scanner works) -----------


def test_synthetic_new_leak_is_detected():
    source = "import os\nfrom google.cloud import storage\n"
    assert _forbidden_sdk_imports_in_source(source) == {"google.cloud.storage"}


def test_detects_plain_import_form():
    assert _forbidden_sdk_imports_in_source("import google.cloud.storage") == {
        "google.cloud.storage"
    }


def test_detects_kubernetes_submodule_import():
    source = "from kubernetes import client, config\nimport kubernetes.stream\n"
    assert _forbidden_sdk_imports_in_source(source) == {
        "kubernetes.client",
        "kubernetes.config",
        "kubernetes.stream",
    }


def test_detects_deferred_import_inside_function():
    source = "def f():\n    from google.cloud import bigquery\n    return bigquery\n"
    assert _forbidden_sdk_imports_in_source(source) == {"google.cloud.bigquery"}


def test_detects_import_under_type_checking_block():
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from google.cloud import compute_v1\n"
    )
    assert _forbidden_sdk_imports_in_source(source) == {"google.cloud.compute_v1"}


def test_clean_source_has_no_leaks():
    source = "import os\nfrom app.services.foo import Bar\nimport googleapiclient\n"
    assert _forbidden_sdk_imports_in_source(source) == set()
