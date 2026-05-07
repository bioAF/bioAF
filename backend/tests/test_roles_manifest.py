"""SA hardening invariant: installer/roles_manifest.yaml is the single source
of truth and must contain the expected sets used by both the installer and
the backend validation probe.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.gcp_config import _APP_PERMS, _BOOTSTRAP_PERMS, _DROPPED_PERMS, _SHARED_PERMS


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MANIFEST_PATH = _REPO_ROOT / "installer" / "roles_manifest.yaml"


@pytest.fixture(scope="module")
def manifest() -> dict:
    if not _MANIFEST_PATH.exists():
        pytest.skip(f"installer/roles_manifest.yaml missing at {_MANIFEST_PATH}")
    return yaml.safe_load(_MANIFEST_PATH.read_text())


def test_manifest_top_level_keys(manifest):
    for key in ("bootstrap", "app", "custom_roles", "dropped", "probe_split"):
        assert key in manifest, f"Manifest missing top-level key: {key}"


def test_manifest_probe_split_matches_backend_constants(manifest):
    split = manifest["probe_split"]
    assert set(split["app"]) == set(_APP_PERMS)
    assert set(split["bootstrap"]) == set(_BOOTSTRAP_PERMS)
    assert set(split["shared"]) == set(_SHARED_PERMS)


def test_manifest_dropped_matches_backend(manifest):
    assert set(manifest["dropped"]) == set(_DROPPED_PERMS)


def test_manifest_includes_bioaf_sa_manager_custom_role(manifest):
    custom_roles = {r["id"]: r for r in manifest["custom_roles"]}
    assert "bioafSaManager" in custom_roles
    role = custom_roles["bioafSaManager"]
    assert set(role["permissions"]) == {
        "iam.serviceAccounts.get",
        "iam.serviceAccounts.list",
        "iam.serviceAccounts.delete",
    }


def test_manifest_app_bindings_have_required_keys(manifest):
    for binding in manifest["app"]:
        assert "role" in binding
        assert "scope" in binding


def test_manifest_drops_keys_create_and_datasets_create(manifest):
    assert "iam.serviceAccountKeys.create" in manifest["dropped"]
    assert "bigquery.datasets.create" in manifest["dropped"]


def test_manifest_bootstrap_includes_the_broad_set(manifest):
    bootstrap_roles = {entry["role"] for entry in manifest["bootstrap"]}
    expected_subset = {
        "roles/storage.admin",
        "roles/iam.serviceAccountAdmin",
        "roles/resourcemanager.projectIamAdmin",
        "roles/cloudbuild.builds.editor",
        "roles/artifactregistry.admin",
    }
    assert expected_subset.issubset(bootstrap_roles)
    assert "roles/iam.serviceAccountKeyAdmin" not in bootstrap_roles, (
        "iam.serviceAccountKeyAdmin should be dropped from the role set"
    )
