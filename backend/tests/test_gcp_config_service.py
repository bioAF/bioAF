"""Unit tests for the GCP credentials validation service.

All GCP API calls are mocked - no real GCP calls are made.
"""

import json
from unittest.mock import MagicMock, patch


from app.services.gcp_config import validate_gcp_credentials


VALID_SA_KEY = json.dumps(
    {
        "type": "service_account",
        "project_id": "my-project",
        "private_key_id": "key123",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----\n",
        "client_email": "bioaf@my-project.iam.gserviceaccount.com",
        "client_id": "123456789",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
)


def _mock_enabled_services(api_names: list[str]) -> MagicMock:
    """Build a mock list_services response with the given enabled API names."""
    services = []
    for name in api_names:
        svc = MagicMock()
        svc.config.name = name
        services.append(svc)
    return services


def _mock_iam_response(permissions: list[str]) -> MagicMock:
    """Build a mock testIamPermissions response returning the given permissions."""
    resp = MagicMock()
    resp.permissions = permissions
    return resp


ALL_REQUIRED_APIS = [
    "cloudresourcemanager.googleapis.com",
    "storage.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "secretmanager.googleapis.com",
    "compute.googleapis.com",
    "pubsub.googleapis.com",
]

ALL_REQUIRED_PERMISSIONS = [
    "storage.buckets.create",
    "pubsub.topics.create",
    "pubsub.topics.getIamPolicy",
    "pubsub.topics.setIamPolicy",
    "container.clusters.create",
    "iam.serviceAccounts.actAs",
    "compute.instances.create",
    "resourcemanager.projects.getIamPolicy",
    "resourcemanager.projects.setIamPolicy",
]


# ---------------------------------------------------------------------------
# Test 1: All checks skipped when credentials cannot be loaded
# ---------------------------------------------------------------------------
def test_invalid_credentials_skips_all_downstream_checks():
    """When credentials fail to load, all downstream checks are skipped."""
    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key="not-valid-json",
    )

    assert result.passed is False
    names = [c.name for c in result.checks]
    assert "credentials_loaded" in names

    cred_check = next(c for c in result.checks if c.name == "credentials_loaded")
    assert cred_check.passed is False

    skipped = [c for c in result.checks if c.status == "skipped"]
    assert len(skipped) >= 4, "Downstream checks must be skipped when creds fail"


# ---------------------------------------------------------------------------
# Test 2: Credential load fails for malformed JSON
# ---------------------------------------------------------------------------
def test_malformed_service_account_key_fails_credential_check():
    """Malformed JSON in the service account key returns a failed credential check."""
    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key="{bad json",
    )
    cred_check = next(c for c in result.checks if c.name == "credentials_loaded")
    assert cred_check.passed is False
    assert result.passed is False


# ---------------------------------------------------------------------------
# Test 3: Project not accessible marks downstream checks as skipped
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_project_not_accessible_skips_downstream(mock_sa, mock_rm):
    """When project access check fails, subsequent checks are skipped."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds

    mock_rm.ProjectsClient.return_value.get_project.side_effect = Exception("403 Permission denied")

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    project_check = next(c for c in result.checks if c.name == "project_accessible")
    assert project_check.passed is False

    skipped = [c for c in result.checks if c.status == "skipped"]
    assert len(skipped) >= 3, "Downstream checks skipped after project failure"
    assert result.passed is False


# ---------------------------------------------------------------------------
# Test 4: Storage API disabled returns failed check
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_storage_api_disabled_marks_check_failed(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """When Cloud Storage API is not enabled the check fails."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(ALL_REQUIRED_PERMISSIONS)
    mock_storage.Client.return_value.list_buckets.side_effect = Exception("Cloud Storage API has not been used")
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    storage_check = next((c for c in result.checks if c.name == "storage_api_enabled"), None)
    assert storage_check is not None
    assert storage_check.passed is False


# ---------------------------------------------------------------------------
# Test 5: vm_default credential source uses google.auth.default
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.google_auth_default")
def test_vm_default_uses_google_auth_default(mock_auth_default, mock_rm, mock_storage, mock_gke, mock_su):
    """When credential_source is 'vm_default', google.auth.default() is called."""
    mock_creds = MagicMock()
    mock_auth_default.return_value = (mock_creds, "my-project")
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(ALL_REQUIRED_PERMISSIONS)
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    validate_gcp_credentials(
        project_id="my-project",
        credential_source="vm_default",
        service_account_key=None,
    )

    mock_auth_default.assert_called_once()


# ---------------------------------------------------------------------------
# Test 6: All checks pass when GCP APIs respond correctly
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_all_checks_pass_with_valid_credentials(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """When all GCP API calls succeed, all checks pass and result.passed is True."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(ALL_REQUIRED_PERMISSIONS)
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    assert result.passed is True
    for check in result.checks:
        assert check.passed is True, f"Check {check.name!r} should have passed"
        assert check.status != "skipped"


# ---------------------------------------------------------------------------
# Test 7: vm_default with service_account_email uses impersonation
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.impersonated_credentials")
@patch("app.services.gcp_config.google_auth_default")
def test_vm_default_with_sa_email_uses_impersonation(
    mock_auth_default, mock_impersonated, mock_rm, mock_storage, mock_gke, mock_su
):
    """When vm_default + service_account_email is set, impersonated credentials are used."""
    mock_source_creds = MagicMock()
    mock_auth_default.return_value = (mock_source_creds, "my-project")
    mock_target_creds = MagicMock()
    mock_impersonated.Credentials.return_value = mock_target_creds

    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(ALL_REQUIRED_PERMISSIONS)
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="vm_default",
        service_account_key=None,
        service_account_email="bioaf-sa@my-project.iam.gserviceaccount.com",
    )

    mock_impersonated.Credentials.assert_called_once()
    call_kwargs = mock_impersonated.Credentials.call_args
    assert call_kwargs.kwargs["target_principal"] == "bioaf-sa@my-project.iam.gserviceaccount.com"

    cred_check = next(c for c in result.checks if c.name == "credentials_loaded")
    assert cred_check.passed is True
    assert "impersonating" in cred_check.message


# ---------------------------------------------------------------------------
# Test 8: GKE API check fails when API is disabled
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_gke_api_disabled_marks_check_failed(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """When GKE API is not enabled, the gke_api_enabled check fails."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(ALL_REQUIRED_PERMISSIONS)
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.side_effect = Exception(
        "Kubernetes Engine API has not been used"
    )
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    gke_check = next(c for c in result.checks if c.name == "gke_api_enabled")
    assert gke_check.passed is False
    assert result.passed is False


# ---------------------------------------------------------------------------
# Test 9: Missing required APIs are reported in iam_permissions check
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_missing_required_apis_reported(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """When required APIs are not enabled, apis_enabled check reports them."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(ALL_REQUIRED_PERMISSIONS)
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()

    # Only some APIs enabled - missing iam and secretmanager
    partial_apis = [
        "cloudresourcemanager.googleapis.com",
        "storage.googleapis.com",
        "container.googleapis.com",
        "compute.googleapis.com",
    ]
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(partial_apis)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    apis_check = next(c for c in result.checks if c.name == "apis_enabled")
    assert apis_check.passed is False
    assert "iam.googleapis.com" in apis_check.message
    assert "secretmanager.googleapis.com" in apis_check.message
    assert result.passed is False


# ---------------------------------------------------------------------------
# Test 10: storage_access is skipped when storage API is disabled
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_storage_access_skipped_when_storage_api_disabled(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """When storage API check fails, storage_access is skipped."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(ALL_REQUIRED_PERMISSIONS)
    mock_storage.Client.return_value.list_buckets.side_effect = Exception("Storage API disabled")
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    storage_access = next(c for c in result.checks if c.name == "storage_access")
    assert storage_access.status == "skipped"


# ---------------------------------------------------------------------------
# Test 11: All six checks are returned in expected order
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_all_seven_checks_returned_in_order(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """Validation returns all seven checks in the expected order."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(ALL_REQUIRED_PERMISSIONS)
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    expected_names = [
        "credentials_loaded",
        "project_accessible",
        "storage_api_enabled",
        "gke_api_enabled",
        "apis_enabled",
        "iam_permissions",
        "storage_access",
    ]
    actual_names = [c.name for c in result.checks]
    assert actual_names == expected_names


# ---------------------------------------------------------------------------
# Test 12: Missing pubsub API is detected
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_missing_pubsub_api_reported(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """When pubsub API is not enabled, apis_enabled check reports it."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(ALL_REQUIRED_PERMISSIONS)
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()

    # All APIs except pubsub
    apis_without_pubsub = [a for a in ALL_REQUIRED_APIS if a != "pubsub.googleapis.com"]
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(apis_without_pubsub)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    apis_check = next(c for c in result.checks if c.name == "apis_enabled")
    assert apis_check.passed is False
    assert "pubsub.googleapis.com" in apis_check.message
    assert result.passed is False


# ---------------------------------------------------------------------------
# Test 13: Missing IAM permissions are reported
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_missing_iam_permissions_reported(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """When SA lacks required permissions, iam_permissions check reports them."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()

    # Return only some permissions (missing pubsub.topics.create and container.clusters.create)
    mock_rm.ProjectsClient.return_value.test_iam_permissions.return_value = _mock_iam_response(
        ["storage.buckets.create", "iam.serviceAccounts.actAs", "compute.instances.create"]
    )

    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    iam_check = next(c for c in result.checks if c.name == "iam_permissions")
    assert iam_check.passed is False
    assert "pubsub.topics.create" in iam_check.message
    assert "container.clusters.create" in iam_check.message
    assert result.passed is False


# ---------------------------------------------------------------------------
# Test 14: IAM permissions check handles API errors gracefully
# ---------------------------------------------------------------------------
@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_iam_permissions_check_handles_api_error(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """When testIamPermissions API call fails, the check fails gracefully."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.side_effect = Exception("403 Forbidden")
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=VALID_SA_KEY,
    )

    iam_check = next(c for c in result.checks if c.name == "iam_permissions")
    assert iam_check.passed is False
    assert result.passed is False
