"""GCP credentials validation service.

Performs ordered checks against GCP APIs. Failed dependencies cascade as
``skipped`` so the caller can distinguish "could not even reach this
check" from "this check ran and failed".

In ``vm_default`` mode the IAM-permissions check splits in two:
- bioaf-app probe (raw ADC) -- holds project-level grants for storage.admin
  (scoped), compute.instanceAdmin.v1 (scoped), bigquery.jobUser, logging
  writer, browser/serviceUsageViewer/secretmanager.viewer, plus per-resource
  bindings managed by Terraform.
- bioaf-bootstrap probe (impersonated) -- holds the broad project-level
  grants needed for IAM/Terraform/Cloud Build operations.

The merged result is the AND of the two probes. Legacy ``service_account_key``
mode keeps the single-probe code path so existing installs see no change.

External GCP names (``service_account``, ``resourcemanager_v3``, ``storage``,
``google_auth_default``, ``impersonated_credentials``, ``service_usage_v1``,
``container_v1``) are module-level for tests to patch.
"""

import json
from pathlib import Path

import google.auth as _google_auth
import yaml
from google.auth import impersonated_credentials as _impersonated_credentials
from google.cloud import container_v1, resourcemanager_v3, storage
from google.cloud import service_usage_v1
from google.oauth2 import service_account

from app.schemas.gcp_config import (
    GCPValidationCheck,
    GCPValidationResult,
    PermissionDetail,
    SAProbeResult,
)

# Aliases for patching in tests
google_auth_default = _google_auth.default
impersonated_credentials = _impersonated_credentials

_GCP_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Maps each tested IAM permission to the role we recommend for granting it.
_PERMISSION_ROLE_MAP: dict[str, str] = {
    "storage.buckets.create": "roles/storage.admin",
    "pubsub.topics.create": "roles/pubsub.admin",
    "pubsub.topics.getIamPolicy": "roles/pubsub.admin",
    "pubsub.topics.setIamPolicy": "roles/pubsub.admin",
    "container.clusters.create": "roles/container.admin",
    "iam.serviceAccounts.actAs": "roles/iam.serviceAccountUser",
    "iam.serviceAccounts.create": "roles/iam.serviceAccountAdmin",
    "iam.serviceAccounts.setIamPolicy": "roles/iam.serviceAccountAdmin",
    "compute.instances.create": "roles/compute.admin",
    "resourcemanager.projects.getIamPolicy": "roles/resourcemanager.projectIamAdmin",
    "resourcemanager.projects.setIamPolicy": "roles/resourcemanager.projectIamAdmin",
    "bigquery.jobs.create": "roles/bigquery.jobUser",
    "artifactregistry.repositories.create": "roles/artifactregistry.admin",
    "cloudbuild.builds.create": "roles/cloudbuild.builds.editor",
    "logging.logEntries.create": "roles/logging.logWriter",
    "serviceusage.services.enable": "roles/serviceusage.serviceUsageAdmin",
}


def _load_manifest() -> dict:
    """Load installer/roles_manifest.yaml. Returns {} if not present."""
    here = Path(__file__).resolve()
    # backend/app/services/gcp_config.py -> repo root via 4 parents
    candidate = here.parent.parent.parent.parent / "installer" / "roles_manifest.yaml"
    if not candidate.exists():
        return {}
    return yaml.safe_load(candidate.read_text()) or {}


_MANIFEST = _load_manifest()
_PROBE_SPLIT = _MANIFEST.get("probe_split", {}) or {}

# Permission ownership for the dual-SA probe. Defaults preserve behavior if
# the manifest is missing in test environments that don't ship the file.
_APP_PERMS: list[str] = list(
    _PROBE_SPLIT.get("app", [])
    or [
        "bigquery.jobs.create",
        "logging.logEntries.create",
    ]
)
_BOOTSTRAP_PERMS: list[str] = list(
    _PROBE_SPLIT.get("bootstrap", [])
    or [
        "storage.buckets.create",
        "pubsub.topics.create",
        "pubsub.topics.getIamPolicy",
        "pubsub.topics.setIamPolicy",
        "container.clusters.create",
        "iam.serviceAccounts.actAs",
        "iam.serviceAccounts.create",
        "iam.serviceAccounts.setIamPolicy",
        "compute.instances.create",
        "resourcemanager.projects.getIamPolicy",
        "resourcemanager.projects.setIamPolicy",
        "artifactregistry.repositories.create",
        "cloudbuild.builds.create",
        "serviceusage.services.enable",
    ]
)
_SHARED_PERMS: list[str] = list(_PROBE_SPLIT.get("shared", []) or [])
_DROPPED_PERMS: list[str] = list(
    _MANIFEST.get("dropped", [])
    or [
        "iam.serviceAccountKeys.create",
        "bigquery.datasets.create",
    ]
)

# Per-SA role lists for UI guidance.
BOOTSTRAP_ROLES: list[str] = [r["role"] for r in (_MANIFEST.get("bootstrap") or [])] or [
    "roles/storage.admin",
    "roles/pubsub.admin",
    "roles/container.admin",
    "roles/iam.serviceAccountUser",
    "roles/iam.serviceAccountAdmin",
    "roles/compute.admin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/bigquery.dataEditor",
    "roles/artifactregistry.admin",
    "roles/cloudbuild.builds.editor",
    "roles/logging.logWriter",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/viewer",
]
APP_ROLES: list[str] = [a["role"] for a in (_MANIFEST.get("app") or [])] or [
    "roles/storage.admin",
    "roles/compute.instanceAdmin.v1",
    "roles/container.admin",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/browser",
    "roles/serviceusage.serviceUsageViewer",
    "roles/secretmanager.viewer",
    "roles/bigquery.jobUser",
    "roles/iam.serviceAccountTokenCreator",
]

# Backwards-compatible deduplicated list of all roles used by the legacy
# single-SA path. Frontend code that expects a single list keeps working.
RECOMMENDED_ROLES: list[str] = list(dict.fromkeys([*_PERMISSION_ROLE_MAP.values(), "roles/viewer"]))

_SKIP_CREDS = "Skipped: credentials failed to load"
_SKIP_PROJECT = "Skipped: project not accessible"


def _skipped(name: str, reason: str) -> GCPValidationCheck:
    return GCPValidationCheck(name=name, passed=False, message=reason, status="skipped")


def _skip_all_after_creds() -> list[GCPValidationCheck]:
    return [
        _skipped("project_accessible", _SKIP_CREDS),
        _skipped("storage_api_enabled", _SKIP_CREDS),
        _skipped("gke_api_enabled", _SKIP_CREDS),
        _skipped("apis_enabled", _SKIP_CREDS),
        _skipped("iam_permissions", _SKIP_CREDS),
        _skipped("storage_access", _SKIP_CREDS),
    ]


def _skip_all_after_project() -> list[GCPValidationCheck]:
    return [
        _skipped("storage_api_enabled", _SKIP_PROJECT),
        _skipped("gke_api_enabled", _SKIP_PROJECT),
        _skipped("apis_enabled", _SKIP_PROJECT),
        _skipped("iam_permissions", _SKIP_PROJECT),
        _skipped("storage_access", _SKIP_PROJECT),
    ]


def _probe_permissions(
    creds, project_id: str, permissions: list[str]
) -> tuple[set[str], list[PermissionDetail], str | None]:
    """Run testIamPermissions for the given SA. Returns (granted, details, error)."""
    try:
        rm_client = resourcemanager_v3.ProjectsClient(credentials=creds)
        resp = rm_client.test_iam_permissions(
            resource=f"projects/{project_id}",
            permissions=permissions,
        )
        granted = set(resp.permissions)
        details = [
            PermissionDetail(
                permission=p,
                granted=p in granted,
                recommended_role=_PERMISSION_ROLE_MAP.get(p, "roles/viewer"),
            )
            for p in permissions
        ]
        return granted, details, None
    except Exception as exc:
        return set(), [], str(exc)


def validate_gcp_credentials(
    project_id: str,
    credential_source: str,
    service_account_key: str | None,
    service_account_email: str | None = None,
) -> GCPValidationResult:
    """Run ordered GCP validation checks and return a result with per-check detail.

    Checks (in order):
    1. credentials_loaded   - can we load/parse the credentials?
    2. project_accessible   - can we fetch the GCP project via Resource Manager?
    3. storage_api_enabled  - is the Cloud Storage API enabled?
    4. gke_api_enabled      - is the Kubernetes Engine API enabled?
    5. apis_enabled         - are all required GCP APIs enabled?
    6. iam_permissions      - does the SA / SA pair have required permissions?
       In vm_default mode this is split into two probes (bioaf-app via ADC,
       bioaf-bootstrap via impersonation) and merged.
    7. storage_access       - can we read/write to a GCS bucket?
    """
    checks: list[GCPValidationCheck] = []

    # Track creds available for downstream probes.
    app_creds = None
    bootstrap_creds = None
    legacy_creds = None
    use_dual_probe = credential_source == "vm_default"

    # ------------------------------------------------------------------
    # Check 1: Load credentials
    # ------------------------------------------------------------------
    try:
        if credential_source == "service_account_key":
            key_data = json.loads(service_account_key or "")
            legacy_creds = service_account.Credentials.from_service_account_info(key_data, scopes=_GCP_SCOPES)
            primary_creds = legacy_creds
            msg = "Credentials loaded successfully"
        else:
            source_creds, _ = google_auth_default(scopes=_GCP_SCOPES)
            app_creds = source_creds
            if service_account_email:
                bootstrap_creds = impersonated_credentials.Credentials(
                    source_credentials=source_creds,
                    target_principal=service_account_email,
                    target_scopes=_GCP_SCOPES,
                )
                primary_creds = bootstrap_creds
                msg = f"Credentials loaded successfully (impersonating {service_account_email})"
            else:
                primary_creds = app_creds
                # Without a bootstrap target we cannot run the bootstrap probe.
                use_dual_probe = False
                msg = "Credentials loaded successfully"

        checks.append(GCPValidationCheck(name="credentials_loaded", passed=True, message=msg))
    except Exception as exc:
        checks.append(GCPValidationCheck(name="credentials_loaded", passed=False, message=str(exc)))
        checks.extend(_skip_all_after_creds())
        return GCPValidationResult(passed=False, checks=checks)

    # ------------------------------------------------------------------
    # Check 2: Project accessible (Cloud Resource Manager API)
    # ------------------------------------------------------------------
    try:
        rm_client = resourcemanager_v3.ProjectsClient(credentials=primary_creds)
        rm_client.get_project(name=f"projects/{project_id}")
        checks.append(
            GCPValidationCheck(
                name="project_accessible",
                passed=True,
                message=f"Project {project_id!r} is accessible",
            )
        )
    except Exception as exc:
        checks.append(GCPValidationCheck(name="project_accessible", passed=False, message=str(exc)))
        checks.extend(_skip_all_after_project())
        return GCPValidationResult(passed=False, checks=checks)

    # ------------------------------------------------------------------
    # Check 3: Storage API enabled (list_buckets as a lightweight probe)
    # ------------------------------------------------------------------
    storage_ok = False
    gcs_client = storage.Client(credentials=primary_creds, project=project_id)
    try:
        list(gcs_client.list_buckets(max_results=1))
        checks.append(
            GCPValidationCheck(
                name="storage_api_enabled",
                passed=True,
                message="Cloud Storage API is enabled and accessible",
            )
        )
        storage_ok = True
    except Exception as exc:
        checks.append(GCPValidationCheck(name="storage_api_enabled", passed=False, message=str(exc)))

    # ------------------------------------------------------------------
    # Check 4: GKE API enabled (Kubernetes Engine API)
    # ------------------------------------------------------------------
    try:
        gke_client = container_v1.ClusterManagerClient(credentials=primary_creds)
        gke_client.list_clusters(parent=f"projects/{project_id}/locations/-")
        checks.append(
            GCPValidationCheck(
                name="gke_api_enabled",
                passed=True,
                message="Kubernetes Engine API is enabled",
            )
        )
    except Exception as exc:
        checks.append(GCPValidationCheck(name="gke_api_enabled", passed=False, message=str(exc)))

    # ------------------------------------------------------------------
    # Check 5: APIs enabled -- verify all required GCP APIs are enabled
    # via Service Usage API
    # ------------------------------------------------------------------
    required_apis = [
        "cloudresourcemanager.googleapis.com",
        "storage.googleapis.com",
        "container.googleapis.com",
        "iam.googleapis.com",
        "secretmanager.googleapis.com",
        "compute.googleapis.com",
        "pubsub.googleapis.com",
        "bigquery.googleapis.com",
        "artifactregistry.googleapis.com",
        "cloudbuild.googleapis.com",
    ]
    try:
        su_client = service_usage_v1.ServiceUsageClient(credentials=primary_creds)
        enabled_resp = su_client.list_services(
            request={
                "parent": f"projects/{project_id}",
                "filter": "state:ENABLED",
                "page_size": 200,
            }
        )
        enabled_names = set()
        for svc in enabled_resp:
            config = svc.config
            if config and config.name:
                enabled_names.add(config.name)

        missing = [api for api in required_apis if api not in enabled_names]
        if missing:
            checks.append(
                GCPValidationCheck(
                    name="apis_enabled",
                    passed=False,
                    message=f"Required APIs not enabled: {', '.join(missing)}",
                )
            )
        else:
            checks.append(
                GCPValidationCheck(
                    name="apis_enabled",
                    passed=True,
                    message="All required APIs are enabled",
                )
            )
    except Exception as exc:
        checks.append(GCPValidationCheck(name="apis_enabled", passed=False, message=str(exc)))

    # ------------------------------------------------------------------
    # Check 6: IAM permissions
    # ------------------------------------------------------------------
    permission_details: list[PermissionDetail] = []
    app_probe: SAProbeResult | None = None
    bootstrap_probe: SAProbeResult | None = None

    if use_dual_probe and app_creds is not None and bootstrap_creds is not None:
        app_perms = _APP_PERMS + _SHARED_PERMS
        bootstrap_perms = _BOOTSTRAP_PERMS + _SHARED_PERMS

        granted_app, app_details, app_err = _probe_permissions(app_creds, project_id, app_perms)
        granted_boot, boot_details, boot_err = _probe_permissions(bootstrap_creds, project_id, bootstrap_perms)

        app_missing = [p for p in app_perms if p not in granted_app]
        boot_missing = [p for p in bootstrap_perms if p not in granted_boot]

        app_probe = SAProbeResult(
            sa_email=None,
            passed=app_err is None and not app_missing,
            checks=[
                GCPValidationCheck(
                    name="iam_permissions",
                    passed=app_err is None and not app_missing,
                    message=app_err
                    or (
                        f"Missing permissions: {', '.join(app_missing)}"
                        if app_missing
                        else "All bioaf-app permissions are granted"
                    ),
                )
            ],
            permission_details=app_details,
        )
        bootstrap_probe = SAProbeResult(
            sa_email=service_account_email,
            passed=boot_err is None and not boot_missing,
            checks=[
                GCPValidationCheck(
                    name="iam_permissions",
                    passed=boot_err is None and not boot_missing,
                    message=boot_err
                    or (
                        f"Missing permissions: {', '.join(boot_missing)}"
                        if boot_missing
                        else "All bioaf-bootstrap permissions are granted"
                    ),
                )
            ],
            permission_details=boot_details,
        )

        merged_passed = app_probe.passed and bootstrap_probe.passed
        permission_details = app_details + boot_details
        if merged_passed:
            checks.append(
                GCPValidationCheck(
                    name="iam_permissions",
                    passed=True,
                    message="All required IAM permissions are granted (app + bootstrap)",
                )
            )
        else:
            parts: list[str] = []
            if not app_probe.passed:
                parts.append(f"bioaf-app: {app_err or 'missing ' + ', '.join(app_missing)}")
            if not bootstrap_probe.passed:
                parts.append(f"bioaf-bootstrap: {boot_err or 'missing ' + ', '.join(boot_missing)}")
            checks.append(
                GCPValidationCheck(
                    name="iam_permissions",
                    passed=False,
                    message="; ".join(parts),
                )
            )
    else:
        # Single-probe: legacy service_account_key OR vm_default with no
        # bootstrap impersonation target configured.
        all_perms = _APP_PERMS + _BOOTSTRAP_PERMS + _SHARED_PERMS
        granted, details, err = _probe_permissions(primary_creds, project_id, all_perms)
        permission_details = details
        missing = [p for p in all_perms if p not in granted]
        if err:
            checks.append(GCPValidationCheck(name="iam_permissions", passed=False, message=err))
        elif missing:
            checks.append(
                GCPValidationCheck(
                    name="iam_permissions",
                    passed=False,
                    message=f"Missing permissions: {', '.join(missing)}",
                )
            )
        else:
            checks.append(
                GCPValidationCheck(
                    name="iam_permissions",
                    passed=True,
                    message="All required IAM permissions are granted",
                )
            )

    # ------------------------------------------------------------------
    # Check 7: Storage write access
    # ------------------------------------------------------------------
    if storage_ok:
        try:
            buckets = list(gcs_client.list_buckets(prefix=f"{project_id}-bioaf", max_results=1))
            if buckets:
                msg = f"Storage bucket {buckets[0].name!r} is accessible"
            else:
                msg = "Storage API accessible (no bioaf buckets found yet -- they will be created during setup)"
            checks.append(GCPValidationCheck(name="storage_access", passed=True, message=msg))
        except Exception as exc:
            checks.append(GCPValidationCheck(name="storage_access", passed=False, message=str(exc)))
    else:
        checks.append(_skipped("storage_access", "Skipped: Storage API not enabled"))

    all_passed = all(c.passed for c in checks)
    return GCPValidationResult(
        passed=all_passed,
        checks=checks,
        recommended_roles=RECOMMENDED_ROLES,
        permission_details=permission_details,
        app_probe=app_probe,
        bootstrap_probe=bootstrap_probe,
    )
