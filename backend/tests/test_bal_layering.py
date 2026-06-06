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


# --- Tree scan: no SDK imports outside adapters ------------------------------

_BACKEND_APP = Path(__file__).resolve().parent.parent / "app"


def _iter_app_modules(*, exclude_adapters: bool):
    """Yield (posix-relative-path, source) for every module under app/.

    When ``exclude_adapters`` is true, modules under ``app/adapters/`` are
    skipped: adapters are the one place allowed to import SDKs.
    """
    for path in sorted(_BACKEND_APP.rglob("*.py")):
        rel = path.relative_to(_BACKEND_APP)
        if exclude_adapters and rel.parts and rel.parts[0] == "adapters":
            continue
        yield rel.as_posix(), path.read_text()


# Every cloud/k8s SDK import that exists today outside adapters/, as
# (module path relative to app/, forbidden dotted import). This set ONLY
# SHRINKS: each later phase drains the leaks it owns and deletes the matching
# entries. A new leak not listed here fails the build; an entry whose import is
# gone fails the build as stale (see the stale-entry test below).
SDK_IMPORT_ALLOWLIST: set[tuple[str, str]] = {
    # Object storage (GCS) -- the dominant leak. Drained in Phase 3.
    ("api/documents.py", "google.cloud.storage"),
    ("api/files.py", "google.cloud.storage"),
    ("api/lab_documents.py", "google.cloud.storage"),
    ("api/literature.py", "google.cloud.storage"),
    ("api/plots.py", "google.cloud.storage"),
    ("main.py", "google.cloud.storage"),
    ("services/backup_service.py", "google.cloud.storage"),
    ("services/cellxgene_image_service.py", "google.cloud.storage"),
    ("services/environment_build_service.py", "google.cloud.storage"),
    ("services/export_service.py", "google.cloud.storage"),
    ("services/gcs_storage.py", "google.cloud.storage"),
    ("services/h5ad_inspector.py", "google.cloud.storage"),
    ("services/ingest_service.py", "google.cloud.storage"),
    ("services/lab_document_upload_service.py", "google.cloud.storage"),
    ("services/lab_glossary_extraction.py", "google.cloud.storage"),
    ("services/literature/agent_review_payload.py", "google.cloud.storage"),
    ("services/literature/upload_service.py", "google.cloud.storage"),
    ("services/notebook_image_service.py", "google.cloud.storage"),
    ("services/pipeline_monitor_service.py", "google.cloud.storage"),
    ("services/pipeline_output_service.py", "google.cloud.storage"),
    ("services/plot_archive_service.py", "google.cloud.storage"),
    ("services/qc/templates/scrnaseq.py", "google.cloud.storage"),
    ("services/qc_dashboard_service.py", "google.cloud.storage"),
    ("services/reference_data_service.py", "google.cloud.storage"),
    ("services/session_output_service.py", "google.cloud.storage"),
    ("services/storage_service.py", "google.cloud.storage"),
    ("services/terraform_executor.py", "google.cloud.storage"),
    ("services/thumbnail_service.py", "google.cloud.storage"),
    ("services/upload_service.py", "google.cloud.storage"),
    # GCS object ops that also do Tier 2 work; the storage import drains in
    # Phase 3, the others in Phase 6/9.
    ("services/gcp_config.py", "google.cloud.storage"),
    ("services/orphaned_resource_service.py", "google.cloud.storage"),
    ("services/stack_deployment.py", "google.cloud.storage"),
    # Compute/notebook Kubernetes leaks. Drained in Phase 5.
    ("services/session_persistence.py", "kubernetes.client"),
    ("services/session_persistence.py", "kubernetes.config"),
    ("services/session_persistence.py", "kubernetes.stream"),
    # GCE capacity probe. Drained in Phase 6.
    ("services/zone_capacity_probe.py", "google.cloud.compute_v1"),
    # Tier 2 platform-service SDKs. Drained in Phase 9 (9A-9G).
    ("services/gcp_config.py", "google.cloud.container_v1"),
    ("services/gcp_config.py", "google.cloud.resourcemanager_v3"),
    ("services/gcp_config.py", "google.cloud.service_usage_v1"),
    ("services/orphaned_resource_service.py", "google.cloud.container_v1"),
    ("services/orphaned_resource_service.py", "google.cloud.iam_admin_v1"),
    ("services/stack_deployment.py", "google.cloud.container_v1"),
    ("services/secrets_service.py", "google.cloud.secretmanager"),
    ("services/billing_export_service.py", "google.cloud.bigquery"),
    ("services/pubsub_listener.py", "google.cloud.pubsub_v1"),
    ("logging_config.py", "google.cloud.logging"),
}


def test_no_cloud_sdk_imports_outside_adapters():
    violations: set[tuple[str, str]] = set()
    for rel, source in _iter_app_modules(exclude_adapters=True):
        for sdk in _forbidden_sdk_imports_in_source(source):
            violations.add((rel, sdk))

    new_leaks = sorted(violations - SDK_IMPORT_ALLOWLIST)
    assert not new_leaks, (
        "New cloud/k8s SDK import(s) outside backend/app/adapters/. Route through "
        "an adapter, or (if intentional and pending a later phase) add to "
        "SDK_IMPORT_ALLOWLIST:\n"
        + "\n".join(f"  {rel}: imports {sdk}" for rel, sdk in new_leaks)
    )


def test_sdk_allowlist_has_no_stale_entries():
    """Every allowlisted leak must still really leak.

    When a phase removes a leak it must also delete the allowlist entry. This
    forces the allowlist to shrink monotonically instead of accumulating dead
    exemptions.
    """
    actual: set[tuple[str, str]] = set()
    for rel, source in _iter_app_modules(exclude_adapters=True):
        for sdk in _forbidden_sdk_imports_in_source(source):
            actual.add((rel, sdk))

    stale = sorted(SDK_IMPORT_ALLOWLIST - actual)
    assert not stale, (
        "Stale SDK_IMPORT_ALLOWLIST entr(ies): the leak is gone but the "
        "allowlist still exempts it. Delete these entries:\n"
        + "\n".join(f"  {rel}: {sdk}" for rel, sdk in stale)
    )


def test_sdk_allowlist_count_is_pinned():
    """Pin the leak count so any change to it is deliberate and reviewed.

    Decrement this as phases drain leaks; it must reach 0 by end of Phase 9.
    """
    assert len(SDK_IMPORT_ALLOWLIST) == 46
