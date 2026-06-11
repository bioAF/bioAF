"""BAL import guardrail (BAL rework, Phase 0; AWS-readiness extensions).

The governing rule of the BioAF Adapter Layer: no module outside
``backend/app/adapters/`` may let a cloud concern escape the adapter seam, and no
module inside ``backend/app/adapters/`` may import from ``backend/app/services/``
(the layering inversion). ``install-gcp.sh`` is the one sanctioned exception and
lives outside ``backend/app``, so it is not scanned here.

A cloud concern escapes the seam in four ways, each with its own monotonically
shrinking allowlist (freeze today's set; fail the build on any NEW leak; fail on
a stale entry whose leak is already gone; pin the count so changes are reviewed):

1. Service SDK imports (``google.cloud`` / ``kubernetes`` / ``boto3``) -- the
   original guard; drains in Phase 9. See ``SDK_IMPORT_ALLOWLIST``.
2. Credential SDK imports (``google.auth`` / ``google.oauth2`` /
   ``google.api_core``) -- a tier the original AST scan missed entirely; drains
   with the backend-aware credential seam. See ``CREDENTIAL_SDK_ALLOWLIST``.
3. GCP CLI shell strings (``gsutil`` / ``gcloud``) -- string-built commands the
   AST scan is structurally blind to; the most dangerous leak because nothing
   caught it. See ``SHELL_CLI_ALLOWLIST``.
4. ``gs://`` scheme literals -- callers baking in the scheme instead of asking
   the adapter to mint URIs via ``resolve_uri``. See ``GS_URI_LITERAL_ALLOWLIST``.

Together these make "is bioAF AWS-ready?" a green/red CI signal instead of a
manual grep hunt, and stop new GCP assumptions taking root while AWS is built.

Detection for (1) and (2) is a static AST scan of import statements, not a runtime
import, so it catches imports at any nesting depth (module level, inside functions,
inside ``TYPE_CHECKING`` blocks). Dynamic, string-based imports
(``importlib.import_module("google.cloud.storage")``) are a known blind spot.
Detection for (3) and (4) is a substring scan of source text, so it is file-level
and deliberately broad (it also freezes mentions in comments/docstrings); a NEW
``gs://`` added to an already-allowlisted file is not caught until that file drains.
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

# Credential / auth SDK prefixes. These sit one tier apart from the service SDKs
# above: they are drained by the credential-seam workstream (generalize
# ``load_gcp_credentials`` into a backend-aware GCP-ADC-vs-AWS-role seam), not by
# the Phase 9 object-store / platform-service drain. They therefore get their own
# allowlist and pinned count below, while sharing this AST scanner via the
# ``prefixes`` argument. The original scanner was blind to these (they are not
# ``google.cloud.*``), so the GCP credential surface escaped the guard entirely.
CREDENTIAL_SDK_PREFIXES = (
    "google.auth",
    "google.oauth2",
    "google.api_core",
)


def _forbidden_sdk_imports_in_source(source: str, prefixes: tuple[str, ...] = FORBIDDEN_SDK_PREFIXES) -> set[str]:
    """Return the precise forbidden dotted paths imported by ``source``.

    ``prefixes`` selects which SDK tier to scan for (service SDKs by default,
    or ``CREDENTIAL_SDK_PREFIXES`` for the credential tier). Matching is identical
    either way:

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
                if _is_forbidden(alias.name, prefixes):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module = node.module
            if not _is_forbidden(module, prefixes):
                continue
            if module in prefixes:
                # `from google.cloud import storage` / `from kubernetes import client`
                for alias in node.names:
                    found.add(f"{module}.{alias.name}")
            else:
                # `from google.cloud.storage import Blob` -- module is the leak.
                found.add(module)
    return found


def _is_forbidden(name: str, prefixes: tuple[str, ...] = FORBIDDEN_SDK_PREFIXES) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


# --- Detector unit tests (no real files; prove the scanner works) -----------


def test_synthetic_new_leak_is_detected():
    source = "import os\nfrom google.cloud import storage\n"
    assert _forbidden_sdk_imports_in_source(source) == {"google.cloud.storage"}


def test_detects_plain_import_form():
    assert _forbidden_sdk_imports_in_source("import google.cloud.storage") == {"google.cloud.storage"}


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
    source = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from google.cloud import compute_v1\n"
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
    # Object storage (GCS). Phase 3 drained all object-CRUD callers (46 -> 19).
    # The entries below remain by deliberate scope decision, not omission:
    #   - gcs_storage.py: object I/O (move/read) was removed in Phase 3 (now via
    #     the adapter); what's left is get_bucket_metrics (bucket-level lifecycle/
    #     versioning enumeration = Tier-2 -> Phase 9) plus get_credentials + path
    #     helpers. The SDK import stays for get_bucket_metrics; drains in Phase 9.
    #   - reference_data_service.py: hands a raw client to the half-built GKE-Job
    #     ReferenceImporter; drains when the importer is addressed.
    #   - storage_service.py / gcp_config.py / orphaned_resource_service.py: these
    #     do bucket-level work (bucket enumeration+lifecycle, whole-bucket delete)
    #     that the owner scoped to Tier-2 -> Phase 9, not the Phase 3 object-store
    #     interface. (Object-store bucket *versioning* + generation-aware delete
    #     were added in Phase 3 for backup_service / stack_deployment.)
    ("services/gcs_storage.py", "google.cloud.storage"),
    ("services/reference_data_service.py", "google.cloud.storage"),
    ("services/storage_service.py", "google.cloud.storage"),  # bucket enum -> Phase 9
    ("services/gcp_config.py", "google.cloud.storage"),  # Tier-2 bundle -> Phase 9
    ("services/orphaned_resource_service.py", "google.cloud.storage"),  # bucket delete -> Phase 9
    # GCE capacity probe was drained in Phase 6 (folded into the GCE work-node
    # adapter as WorkNodeProvider.probe_zone_capacity; the compute_v1 import now
    # lives in adapters/work_nodes/gce_capacity.py).
    # Tier 2 platform-service SDKs. Drained in Phase 9 (9A-9G).
    ("services/gcp_config.py", "google.cloud.container_v1"),
    ("services/gcp_config.py", "google.cloud.resourcemanager_v3"),
    ("services/gcp_config.py", "google.cloud.service_usage_v1"),
    ("services/orphaned_resource_service.py", "google.cloud.container_v1"),
    # iam_admin_v1 drained in Phase 9B (routed through adapters/iam/IamProvider).
    ("services/stack_deployment.py", "google.cloud.container_v1"),
    # bigquery drained in Phase 9D (routed through adapters/billing/BillingProvider).
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
        "SDK_IMPORT_ALLOWLIST:\n" + "\n".join(f"  {rel}: imports {sdk}" for rel, sdk in new_leaks)
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
        "allowlist still exempts it. Delete these entries:\n" + "\n".join(f"  {rel}: {sdk}" for rel, sdk in stale)
    )


def test_sdk_allowlist_count_is_pinned():
    """Pin the leak count so any change to it is deliberate and reviewed.

    Decrement this as phases drain leaks; it must reach 0 by end of Phase 9.
    """
    assert len(SDK_IMPORT_ALLOWLIST) == 10


# --- Tree scan: no adapter imports services (the layering inversion) ---------

_ADAPTERS = _BACKEND_APP / "adapters"


def _service_imports_in_source(source: str) -> set[str]:
    """Return the ``app.services`` modules imported by ``source``.

    ``from app.services import x`` -> ``{"app.services.x"}``.
    ``from app.services.x import Y`` -> ``{"app.services.x"}``.
    ``import app.services.x`` -> ``{"app.services.x"}``.
    """
    found: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.services" or alias.name.startswith("app.services."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module = node.module
            if module == "app.services":
                for alias in node.names:
                    found.add(f"app.services.{alias.name}")
            elif module.startswith("app.services."):
                found.add(module)
    return found


def _iter_adapter_modules():
    for path in sorted(_ADAPTERS.rglob("*.py")):
        yield path.relative_to(_BACKEND_APP).as_posix(), path.read_text()


# Every adapter -> service import that remains, as (adapter path relative to
# app/, imported app.services module). Phase 1 relocated config/credentials
# into app/platform/ and drained those entries. Phase 3 folded GcsStorageService's
# bucket metrics into storage/gcs.py, draining its entry. Phase 5 drained the
# last two (notebooks' session_persistence command builders + session_output_service
# gsutil parser, moved into the adapter package / app.pipeline). The inversion is
# now fully closed: adapters depend only on app.platform / app.pipeline leaves.
ADAPTER_SERVICE_IMPORT_ALLOWLIST: set[tuple[str, str]] = set()


def test_service_import_detector_matches_both_forms():
    source = (
        "from app.services import session_persistence\n"
        "from app.services.gcs_storage import GcsStorageService\n"
        "import app.services.foo\n"
        "from app.platform import credential_injector\n"
    )
    assert _service_imports_in_source(source) == {
        "app.services.session_persistence",
        "app.services.gcs_storage",
        "app.services.foo",
    }


def test_service_import_detector_ignores_non_service_imports():
    source = "from app.platform import config\nfrom app.models import User\n"
    assert _service_imports_in_source(source) == set()


def test_no_adapter_imports_services():
    violations: set[tuple[str, str]] = set()
    for rel, source in _iter_adapter_modules():
        for svc in _service_imports_in_source(source):
            violations.add((rel, svc))

    new_inversions = sorted(violations - ADAPTER_SERVICE_IMPORT_ALLOWLIST)
    assert not new_inversions, (
        "Adapter(s) import from app.services (layering inversion). Depend on "
        "app.platform instead, or (pending Phase 1) add to "
        "ADAPTER_SERVICE_IMPORT_ALLOWLIST:\n" + "\n".join(f"  {rel}: imports {svc}" for rel, svc in new_inversions)
    )


def test_adapter_service_allowlist_has_no_stale_entries():
    actual: set[tuple[str, str]] = set()
    for rel, source in _iter_adapter_modules():
        for svc in _service_imports_in_source(source):
            actual.add((rel, svc))

    stale = sorted(ADAPTER_SERVICE_IMPORT_ALLOWLIST - actual)
    assert not stale, (
        "Stale ADAPTER_SERVICE_IMPORT_ALLOWLIST entr(ies): the inversion is "
        "gone but the allowlist still exempts it. Delete these entries:\n"
        + "\n".join(f"  {rel}: {svc}" for rel, svc in stale)
    )


def test_adapter_service_allowlist_count_is_pinned():
    """Pin the inversion count. Phase 1 drained config/credentials (11 -> 3);
    Phase 3 drained storage/gcs.py (3 -> 2); Phase 5 drained the last two (2 -> 0).
    The adapter->service inversion is now fully closed."""
    assert len(ADAPTER_SERVICE_IMPORT_ALLOWLIST) == 0


# --- Tree scan: the platform layer is leaf-ward (Phase 1) --------------------

_PLATFORM = _BACKEND_APP / "platform"


def _upward_imports_in_source(source: str) -> set[str]:
    """Return any app.services / app.adapters modules imported by ``source``.

    The platform layer sits beneath both services and adapters: it may import
    app.config, app.database, app.models and leaf helpers, but never reach up
    into app.services or app.adapters.
    """
    found: set[str] = set()
    tree = ast.parse(source)
    upward = ("app.services", "app.adapters")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == u or alias.name.startswith(u + ".") for u in upward):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module = node.module
            if module in upward:
                for alias in node.names:
                    found.add(f"{module}.{alias.name}")
            elif any(module.startswith(u + ".") for u in upward):
                found.add(module)
    return found


def test_platform_layer_has_no_upward_imports():
    """app/platform/ must not import app.services or app.adapters.

    This is what makes app.platform a layer adapters may depend on without
    creating a cycle. There is no allowlist: the platform layer is clean by
    construction from Phase 1 onward.
    """
    violations: list[str] = []
    if _PLATFORM.exists():
        for path in sorted(_PLATFORM.rglob("*.py")):
            rel = path.relative_to(_BACKEND_APP).as_posix()
            for mod in _upward_imports_in_source(path.read_text()):
                violations.append(f"  {rel}: imports {mod}")
    assert not violations, (
        "app/platform/ must not import app.services or app.adapters (it is a "
        "leaf-ward layer):\n" + "\n".join(sorted(violations))
    )


# --- Tree scan: no GCP credential SDK imports outside adapters ----------------
#
# A second SDK tier the original import guard missed. ``google.auth`` /
# ``google.oauth2`` / ``google.api_core`` are not ``google.cloud.*``, so every
# credential-resolution import outside adapters/ was invisible to the build.
# Same monotonically-shrinking allowlist contract as SDK_IMPORT_ALLOWLIST; this
# one drains when the backend-aware credential seam lands (GCP ADC / SA-key vs
# AWS role / keys), at which point GCP credential SDK use moves under adapters/.

CREDENTIAL_SDK_ALLOWLIST: set[tuple[str, str]] = {
    ("api/billing_export.py", "google.api_core.exceptions"),
    ("platform/credential_injector.py", "google.auth"),
    ("platform/credential_injector.py", "google.auth.credentials"),
    ("platform/credential_injector.py", "google.auth.impersonated_credentials"),
    ("platform/credential_injector.py", "google.oauth2.service_account"),
    ("services/billing_export_service.py", "google.auth.credentials"),
    ("services/cellxgene_image_service.py", "google.auth"),
    ("services/cellxgene_image_service.py", "google.auth.transport.requests"),
    ("services/gcp_config.py", "google.auth"),
    ("services/gcp_config.py", "google.auth.impersonated_credentials"),
    ("services/gcp_config.py", "google.oauth2.service_account"),
    ("services/notebook_image_service.py", "google.auth"),
    ("services/notebook_image_service.py", "google.auth.transport.requests"),
    ("services/sheets_reader_sa_service.py", "google.auth"),
    ("services/sheets_reader_sa_service.py", "google.auth.impersonated_credentials"),
    ("services/sheets_reader_sa_service.py", "google.oauth2.service_account"),
    ("services/stack_deployment.py", "google.oauth2.service_account"),
}


def test_credential_sdk_detector_finds_auth_imports():
    source = (
        "import google.auth as _ga\n"
        "from google.auth import impersonated_credentials\n"
        "import google.auth.transport.requests\n"
        "from google.oauth2 import service_account\n"
        "from google.api_core.exceptions import Forbidden\n"
    )
    assert _forbidden_sdk_imports_in_source(source, CREDENTIAL_SDK_PREFIXES) == {
        "google.auth",
        "google.auth.impersonated_credentials",
        "google.auth.transport.requests",
        "google.oauth2.service_account",
        "google.api_core.exceptions",
    }


def test_credential_detector_ignores_service_sdk_and_clean_imports():
    # The credential scan must not double-count google.cloud.* (owned by the
    # service-SDK guard) and must ignore unrelated third-party auth libraries.
    source = "from google.cloud import storage\nimport authlib\nimport os\n"
    assert _forbidden_sdk_imports_in_source(source, CREDENTIAL_SDK_PREFIXES) == set()


def test_no_credential_sdk_imports_outside_adapters():
    violations: set[tuple[str, str]] = set()
    for rel, source in _iter_app_modules(exclude_adapters=True):
        for sdk in _forbidden_sdk_imports_in_source(source, CREDENTIAL_SDK_PREFIXES):
            violations.add((rel, sdk))

    new_leaks = sorted(violations - CREDENTIAL_SDK_ALLOWLIST)
    assert not new_leaks, (
        "New GCP credential SDK import(s) outside backend/app/adapters/. Route "
        "credential resolution through the credential seam, or (if pending the "
        "credential-seam workstream) add to CREDENTIAL_SDK_ALLOWLIST:\n"
        + "\n".join(f"  {rel}: imports {sdk}" for rel, sdk in new_leaks)
    )


def test_credential_sdk_allowlist_has_no_stale_entries():
    actual: set[tuple[str, str]] = set()
    for rel, source in _iter_app_modules(exclude_adapters=True):
        for sdk in _forbidden_sdk_imports_in_source(source, CREDENTIAL_SDK_PREFIXES):
            actual.add((rel, sdk))

    stale = sorted(CREDENTIAL_SDK_ALLOWLIST - actual)
    assert not stale, (
        "Stale CREDENTIAL_SDK_ALLOWLIST entr(ies): the leak is gone but the "
        "allowlist still exempts it. Delete these entries:\n" + "\n".join(f"  {rel}: {sdk}" for rel, sdk in stale)
    )


def test_credential_sdk_allowlist_count_is_pinned():
    """Pin the credential-leak count so any change to it is deliberate and reviewed.

    Decrement this as the credential seam drains leaks; target 0 once all
    credential resolution is backend-aware and behind adapters/.
    """
    assert len(CREDENTIAL_SDK_ALLOWLIST) == 17


# --- Tree scan: no GCP CLI shell strings outside adapters --------------------
#
# Leak 2 in the AWS readiness audit, and the most dangerous because nothing
# caught it: the SDK guards are AST import scans and are structurally blind to
# `gsutil` / `gcloud` invocations built as plain strings (f-strings, argv lists,
# Dockerfile RUN lines). They shell out to the GCP CLIs and fail silently on a
# non-GCP install. Substring match is deliberately broad: it also freezes
# mentions in comments/docstrings, which is fine -- the contract is "freeze
# today's set, fail the build on any NEW file." Drains as callers move to
# adapter methods (or cloud_provider-selected commands).

SHELL_CLI_TOKENS = ("gsutil", "gcloud")

SHELL_CLI_ALLOWLIST: set[tuple[str, str]] = {
    ("api/ssh_connect.py", "gcloud"),
    ("services/custom_pipeline_service.py", "gcloud"),
    ("services/custom_pipeline_service.py", "gsutil"),
    ("services/environment_build_service.py", "gcloud"),
    ("services/environment_build_service.py", "gsutil"),
    ("services/notebook_image_service.py", "gsutil"),
}


def _shell_cli_tokens_in_source(source: str) -> set[str]:
    """Return the GCP CLI tokens (``gsutil`` / ``gcloud``) present in ``source``."""
    return {token for token in SHELL_CLI_TOKENS if token in source}


def test_shell_cli_detector_finds_command_strings():
    assert _shell_cli_tokens_in_source('cmd = "gsutil cp src dst"') == {"gsutil"}
    assert _shell_cli_tokens_in_source('run(f"gcloud storage cp {x} {y}")') == {"gcloud"}
    assert _shell_cli_tokens_in_source('argv = ["gcloud", "auth"]\nlog("gsutil")') == {"gcloud", "gsutil"}


def test_shell_cli_detector_ignores_non_gcp_shell():
    assert _shell_cli_tokens_in_source('subprocess.run(["aws", "s3", "cp", src, dst])') == set()


def test_no_gcp_cli_shell_strings_outside_adapters():
    violations: set[tuple[str, str]] = set()
    for rel, source in _iter_app_modules(exclude_adapters=True):
        for token in _shell_cli_tokens_in_source(source):
            violations.add((rel, token))

    new_leaks = sorted(violations - SHELL_CLI_ALLOWLIST)
    assert not new_leaks, (
        "New GCP CLI shell string(s) outside backend/app/adapters/. Replace with "
        "an adapter method (or a cloud_provider-selected command), or (if pending "
        "the Leak-2 drain) add to SHELL_CLI_ALLOWLIST:\n"
        + "\n".join(f"  {rel}: builds {token}" for rel, token in new_leaks)
    )


def test_shell_cli_allowlist_has_no_stale_entries():
    actual: set[tuple[str, str]] = set()
    for rel, source in _iter_app_modules(exclude_adapters=True):
        for token in _shell_cli_tokens_in_source(source):
            actual.add((rel, token))

    stale = sorted(SHELL_CLI_ALLOWLIST - actual)
    assert not stale, (
        "Stale SHELL_CLI_ALLOWLIST entr(ies): the shell string is gone but the "
        "allowlist still exempts it. Delete these entries:\n" + "\n".join(f"  {rel}: {token}" for rel, token in stale)
    )


def test_shell_cli_allowlist_count_is_pinned():
    """Pin the shell-string leak count; decrement as Leak 2 drains. Target 0."""
    assert len(SHELL_CLI_ALLOWLIST) == 6


# --- Tree scan: no gs:// literals outside adapters ---------------------------
#
# Leak 3. The storage adapter treats gs:// as an opaque scheme and exposes
# resolve_uri(store, key) to mint URIs; callers that bake the literal in defeat
# that and break on an S3 install. The adapter is the one place gs:// legitimately
# appears, so the scan excludes it. File-level granularity (same coarse contract
# as the SDK guards): a file stays allowlisted until ALL its gs:// literals are
# gone via resolve_uri(). Known limitation, accepted: a NEW gs:// added to an
# already-allowlisted file is not caught until that file is drained.

GS_URI_LITERAL_ALLOWLIST: set[str] = {
    "services/backup_service.py",
    "services/custom_pipeline_service.py",
    "services/environment_build_service.py",
}


def _gs_uri_in_source(source: str) -> bool:
    """True if ``source`` contains a hardcoded ``gs://`` literal."""
    return "gs://" in source


def test_gs_uri_detector_matches_only_gcs_scheme():
    assert _gs_uri_in_source('uri = "gs://bucket/key"') is True
    assert _gs_uri_in_source('uri = "s3://bucket/key"') is False
    assert _gs_uri_in_source("path = '/local/gs/file'") is False


def test_no_gs_uri_literals_outside_adapters():
    violations = {rel for rel, source in _iter_app_modules(exclude_adapters=True) if _gs_uri_in_source(source)}

    new_leaks = sorted(violations - GS_URI_LITERAL_ALLOWLIST)
    assert not new_leaks, (
        "New gs:// literal(s) outside backend/app/adapters/. Mint URIs via "
        "get_storage_adapter().resolve_uri(store, key) instead, or (if pending "
        "the Leak-3 drain) add to GS_URI_LITERAL_ALLOWLIST:\n" + "\n".join(f"  {rel}" for rel in new_leaks)
    )


def test_gs_uri_allowlist_has_no_stale_entries():
    actual = {rel for rel, source in _iter_app_modules(exclude_adapters=True) if _gs_uri_in_source(source)}

    stale = sorted(GS_URI_LITERAL_ALLOWLIST - actual)
    assert not stale, (
        "Stale GS_URI_LITERAL_ALLOWLIST entr(ies): the file no longer contains a "
        "gs:// literal. Delete these entries:\n" + "\n".join(f"  {rel}" for rel in stale)
    )


def test_gs_uri_allowlist_count_is_pinned():
    """Pin the gs:// leak file count; decrement as Leak 3 drains. Target 0."""
    assert len(GS_URI_LITERAL_ALLOWLIST) == 3
