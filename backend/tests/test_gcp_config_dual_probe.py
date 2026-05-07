"""Tests for the dual-SA validation probe (Breakage 8).

Under SA hardening, no single SA holds all 18 permissions tested by
testIamPermissions. validate_gcp_credentials must run two probes:
- bioaf-app via raw ADC
- bioaf-bootstrap via impersonation

Merged result: passes only if both pass. Per-probe details available
in app_probe / bootstrap_probe fields.
"""

from unittest.mock import MagicMock, patch

from app.services.gcp_config import (
    _APP_PERMS,
    _BOOTSTRAP_PERMS,
    _DROPPED_PERMS,
    _SHARED_PERMS,
    validate_gcp_credentials,
)
from app.services.gcp_config import APP_ROLES, BOOTSTRAP_ROLES


def _mock_iam_response(permissions: list[str]) -> MagicMock:
    resp = MagicMock()
    resp.permissions = permissions
    return resp


def _mock_enabled_services(api_names: list[str]) -> list[MagicMock]:
    services = []
    for name in api_names:
        svc = MagicMock()
        svc.config.name = name
        services.append(svc)
    return services


ALL_REQUIRED_APIS = [
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


def test_permission_split_constants_partition_all_required():
    """The three permission lists together cover the testable permissions
    and exclude the explicitly-dropped ones.
    """
    union = set(_APP_PERMS) | set(_BOOTSTRAP_PERMS) | set(_SHARED_PERMS)
    assert union, "Expected non-empty permission split"
    # No overlap between the partitions.
    assert not (set(_APP_PERMS) & set(_BOOTSTRAP_PERMS))
    assert not (set(_APP_PERMS) & set(_SHARED_PERMS))
    assert not (set(_BOOTSTRAP_PERMS) & set(_SHARED_PERMS))
    # Dropped permissions must NOT be in any of the probe sets.
    for dropped in _DROPPED_PERMS:
        assert dropped not in union, f"{dropped} should be dropped, not probed"


def test_app_and_bootstrap_role_lists_are_distinct():
    """The role lists exposed to the UI separate app/bootstrap responsibilities."""
    assert APP_ROLES, "App role list must not be empty"
    assert BOOTSTRAP_ROLES, "Bootstrap role list must not be empty"
    # Bootstrap holds the broad project-level grants.
    assert "roles/storage.admin" in BOOTSTRAP_ROLES
    # App keeps storage scoped via condition; the broad role should NOT be on app.
    assert "roles/iam.serviceAccountTokenCreator" in {r for r in APP_ROLES}


@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.impersonated_credentials")
@patch("app.services.gcp_config.google_auth_default")
def test_vm_default_dual_probe_both_pass(
    mock_auth_default,
    mock_impersonated,
    mock_rm,
    mock_storage,
    mock_gke,
    mock_su,
):
    """Both probes pass -> merged passed=True; both probe sub-results populated."""
    mock_source = MagicMock(name="adc_source")
    mock_auth_default.return_value = (mock_source, "my-project")
    mock_imp = MagicMock(name="impersonated")
    mock_impersonated.Credentials.return_value = mock_imp

    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    # testIamPermissions echoes back whatever permissions were asked.
    mock_rm.ProjectsClient.return_value.test_iam_permissions.side_effect = lambda resource, permissions: (
        _mock_iam_response(list(permissions))
    )
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="vm_default",
        service_account_key=None,
        service_account_email="bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    )

    assert result.app_probe is not None
    assert result.bootstrap_probe is not None
    assert result.app_probe.passed
    assert result.bootstrap_probe.passed
    assert result.passed
    # Bootstrap probe principal email matches the impersonation target.
    assert result.bootstrap_probe.sa_email == "bioaf-bootstrap@my-project.iam.gserviceaccount.com"


@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.impersonated_credentials")
@patch("app.services.gcp_config.google_auth_default")
def test_vm_default_dual_probe_app_missing_permission(
    mock_auth_default,
    mock_impersonated,
    mock_rm,
    mock_storage,
    mock_gke,
    mock_su,
):
    """If the app probe is missing a permission, merged passed=False, app_probe.passed=False."""
    mock_source = MagicMock(name="adc_source")
    mock_auth_default.return_value = (mock_source, "my-project")
    mock_imp = MagicMock(name="impersonated")
    mock_impersonated.Credentials.return_value = mock_imp

    # Track whether the probe request used app or bootstrap creds by
    # the credentials passed when constructing ProjectsClient. The mock
    # reuses the same client across calls, so we use a side_effect that
    # checks the requested permissions against _APP_PERMS to decide.
    def fake_test_iam_permissions(resource, permissions):
        perms = list(permissions)
        # logging.logEntries.create is in the app probe set; drop it to
        # simulate missing access from bioaf-app while bootstrap stays full.
        if "logging.logEntries.create" in perms:
            return _mock_iam_response([p for p in perms if p != "logging.logEntries.create"])
        return _mock_iam_response(perms)

    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.side_effect = fake_test_iam_permissions
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="vm_default",
        service_account_key=None,
        service_account_email="bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    )

    assert result.app_probe is not None
    assert result.bootstrap_probe is not None
    assert not result.app_probe.passed
    assert result.bootstrap_probe.passed
    assert not result.passed


@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.impersonated_credentials")
@patch("app.services.gcp_config.google_auth_default")
def test_vm_default_dual_probe_bootstrap_missing_permission(
    mock_auth_default,
    mock_impersonated,
    mock_rm,
    mock_storage,
    mock_gke,
    mock_su,
):
    """If the bootstrap probe is missing a permission, merged passed=False, bootstrap_probe.passed=False."""
    mock_source = MagicMock(name="adc_source")
    mock_auth_default.return_value = (mock_source, "my-project")
    mock_imp = MagicMock(name="impersonated")
    mock_impersonated.Credentials.return_value = mock_imp

    def fake_test_iam_permissions(resource, permissions):
        perms = list(permissions)
        # cloudbuild.builds.create lives only in bootstrap perms.
        if "cloudbuild.builds.create" in perms:
            return _mock_iam_response([p for p in perms if p != "cloudbuild.builds.create"])
        return _mock_iam_response(perms)

    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.side_effect = fake_test_iam_permissions
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="vm_default",
        service_account_key=None,
        service_account_email="bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    )

    assert result.app_probe is not None
    assert result.bootstrap_probe is not None
    assert result.app_probe.passed
    assert not result.bootstrap_probe.passed
    assert not result.passed


@patch("app.services.gcp_config.service_usage_v1")
@patch("app.services.gcp_config.container_v1")
@patch("app.services.gcp_config.storage")
@patch("app.services.gcp_config.resourcemanager_v3")
@patch("app.services.gcp_config.service_account")
def test_service_account_key_mode_skips_dual_probe(mock_sa, mock_rm, mock_storage, mock_gke, mock_su):
    """Legacy key mode keeps the single-probe code path; probe sub-results stay None."""
    mock_creds = MagicMock()
    mock_sa.Credentials.from_service_account_info.return_value = mock_creds
    mock_rm.ProjectsClient.return_value.get_project.return_value = MagicMock()
    mock_rm.ProjectsClient.return_value.test_iam_permissions.side_effect = lambda resource, permissions: (
        _mock_iam_response(list(permissions))
    )
    mock_storage.Client.return_value.list_buckets.return_value = []
    mock_gke.ClusterManagerClient.return_value.list_clusters.return_value = MagicMock()
    mock_su.ServiceUsageClient.return_value.list_services.return_value = _mock_enabled_services(ALL_REQUIRED_APIS)

    import json as _json

    fake_key = _json.dumps(
        {
            "type": "service_account",
            "project_id": "my-project",
            "private_key_id": "k",
            "client_email": "legacy@my-project.iam.gserviceaccount.com",
        }
    )
    result = validate_gcp_credentials(
        project_id="my-project",
        credential_source="service_account_key",
        service_account_key=fake_key,
    )

    assert result.app_probe is None
    assert result.bootstrap_probe is None
    assert result.passed
