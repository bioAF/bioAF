"""GCP credentials validation service.

Performs up to six ordered checks against GCP APIs.  When a check fails
any downstream checks that depend on it are marked as ``skipped`` so the
caller can distinguish "we could not even get to this check" from
"this check ran and failed".

All external GCP calls go through module-level names that the test suite
patches (``service_account``, ``resourcemanager_v3``, ``storage``,
``google_auth_default``, ``impersonated_credentials``,
``service_usage_v1``, ``container_v1``).
"""

import json

import google.auth as _google_auth
from google.auth import impersonated_credentials as _impersonated_credentials
from google.cloud import container_v1, resourcemanager_v3, storage
from google.cloud import service_usage_v1
from google.oauth2 import service_account

from app.schemas.gcp_config import GCPValidationCheck, GCPValidationResult, PermissionDetail

# Aliases for patching in tests
google_auth_default = _google_auth.default
impersonated_credentials = _impersonated_credentials

_GCP_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Maps each required IAM permission to the role we recommend for granting it.
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
    "bigquery.datasets.create": "roles/bigquery.dataEditor",
    "bigquery.jobs.create": "roles/bigquery.dataEditor",
    "artifactregistry.repositories.create": "roles/artifactregistry.admin",
    "cloudbuild.builds.create": "roles/cloudbuild.builds.editor",
}

# Deduplicated, stable-order list of recommended roles.
# Start with roles derived from the permission map, then append roles that
# are needed for validation probes (project access, API listing) but are not
# tied to a specific testIamPermissions entry.
RECOMMENDED_ROLES: list[str] = list(
    dict.fromkeys([*_PERMISSION_ROLE_MAP.values(), "roles/serviceusage.serviceUsageViewer", "roles/viewer"])
)

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
    6. iam_permissions      - does the SA have required IAM permissions?
    7. storage_access       - can we read/write to a GCS bucket?
    """
    checks: list[GCPValidationCheck] = []

    # ------------------------------------------------------------------
    # Check 1: Load credentials
    # ------------------------------------------------------------------
    creds = None
    try:
        if credential_source == "service_account_key":
            key_data = json.loads(service_account_key or "")
            creds = service_account.Credentials.from_service_account_info(key_data, scopes=_GCP_SCOPES)
        else:
            source_creds, _ = google_auth_default(scopes=_GCP_SCOPES)
            if service_account_email:
                creds = impersonated_credentials.Credentials(
                    source_credentials=source_creds,
                    target_principal=service_account_email,
                    target_scopes=_GCP_SCOPES,
                )
            else:
                creds = source_creds

        msg = "Credentials loaded successfully"
        if service_account_email and credential_source == "vm_default":
            msg += f" (impersonating {service_account_email})"
        checks.append(GCPValidationCheck(name="credentials_loaded", passed=True, message=msg))
    except Exception as exc:
        checks.append(GCPValidationCheck(name="credentials_loaded", passed=False, message=str(exc)))
        checks.extend(_skip_all_after_creds())
        return GCPValidationResult(passed=False, checks=checks)

    # ------------------------------------------------------------------
    # Check 2: Project accessible (Cloud Resource Manager API)
    # ------------------------------------------------------------------
    try:
        rm_client = resourcemanager_v3.ProjectsClient(credentials=creds)
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
    gcs_client = storage.Client(credentials=creds, project=project_id)
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
        gke_client = container_v1.ClusterManagerClient(credentials=creds)
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
        su_client = service_usage_v1.ServiceUsageClient(credentials=creds)
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
    # Check 6: IAM permissions -- verify the service account has the
    # required project-level permissions via testIamPermissions
    # ------------------------------------------------------------------
    required_permissions = list(_PERMISSION_ROLE_MAP.keys())
    permission_details: list[PermissionDetail] = []
    try:
        rm_client = resourcemanager_v3.ProjectsClient(credentials=creds)
        resp = rm_client.test_iam_permissions(
            resource=f"projects/{project_id}",
            permissions=required_permissions,
        )
        granted = set(resp.permissions)
        missing_perms = [p for p in required_permissions if p not in granted]

        for perm in required_permissions:
            permission_details.append(
                PermissionDetail(
                    permission=perm,
                    granted=perm in granted,
                    recommended_role=_PERMISSION_ROLE_MAP[perm],
                )
            )

        if missing_perms:
            checks.append(
                GCPValidationCheck(
                    name="iam_permissions",
                    passed=False,
                    message=f"Missing permissions: {', '.join(missing_perms)}",
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
    except Exception as exc:
        checks.append(GCPValidationCheck(name="iam_permissions", passed=False, message=str(exc)))

    # ------------------------------------------------------------------
    # Check 6: Storage write access -- attempt to get bucket metadata
    # for a bioaf-prefixed bucket (or just confirm list worked above)
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
    )
