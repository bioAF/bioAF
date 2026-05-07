"""Tests for the GCP credential injection helper (Step 4 - Phase 17).

Tests 8-9 from the spec:
- Test 8: VM default credentials - sets only TF_VAR_project_id/region/zone, no key file
- Test 9: Service account key credentials - writes key to temp file, sets
  GOOGLE_APPLICATION_CREDENTIALS, returns cleanup function

SA hardening tests (vm_default impersonation):
- vm_default + gcp_bootstrap_sa_email -> impersonated creds with bootstrap email
- vm_default + only legacy gcp_service_account_email -> falls back to legacy field
- vm_default + neither field -> raw ADC credentials
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.credential_injector import GCPCredentialInjector, load_gcp_credentials


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vm_default_config() -> dict:
    return {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
        "gcp_zone": "us-central1-a",
    }


def _sa_key_config(key_json: str) -> dict:
    return {
        "gcp_credential_source": "service_account_key",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
        "gcp_zone": "us-central1-a",
        "gcp_service_account_key": key_json,
    }


FAKE_SA_KEY = json.dumps(
    {
        "type": "service_account",
        "project_id": "my-project",
        "private_key_id": "abc123",
        "client_email": "bioaf@my-project.iam.gserviceaccount.com",
    }
)


# ---------------------------------------------------------------------------
# Test 8: VM default credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vm_default_credential_env_vars():
    """VM default source sets TF_VAR_* env vars, no GOOGLE_APPLICATION_CREDENTIALS."""
    env, cleanup = await GCPCredentialInjector.build_env(config=_vm_default_config())

    assert env.get("TF_VAR_project_id") == "my-project"
    assert env.get("TF_VAR_region") == "us-central1"
    assert env.get("TF_VAR_zone") == "us-central1-a"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env


@pytest.mark.asyncio
async def test_vm_default_cleanup_is_noop():
    """VM default source cleanup function does nothing and does not raise."""
    _env, cleanup = await GCPCredentialInjector.build_env(config=_vm_default_config())
    # Should not raise
    await cleanup()


# ---------------------------------------------------------------------------
# Test 9: Service account key credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sa_key_writes_temp_file_and_sets_env():
    """SA key source writes key JSON to a temp file and sets GOOGLE_APPLICATION_CREDENTIALS."""
    env, cleanup = await GCPCredentialInjector.build_env(config=_sa_key_config(FAKE_SA_KEY))

    assert "GOOGLE_APPLICATION_CREDENTIALS" in env
    key_path = Path(env["GOOGLE_APPLICATION_CREDENTIALS"])
    assert key_path.exists()
    assert key_path.read_text() == FAKE_SA_KEY

    # Also sets TF_VAR_ variables
    assert env.get("TF_VAR_project_id") == "my-project"

    # Cleanup removes the file
    await cleanup()
    assert not key_path.exists()


@pytest.mark.asyncio
async def test_sa_key_cleanup_removes_temp_file():
    """Cleanup callable deletes the temp credentials file."""
    env, cleanup = await GCPCredentialInjector.build_env(config=_sa_key_config(FAKE_SA_KEY))
    key_path = Path(env["GOOGLE_APPLICATION_CREDENTIALS"])
    assert key_path.exists()
    await cleanup()
    assert not key_path.exists()


@pytest.mark.asyncio
async def test_missing_sa_key_raises_value_error():
    """Service account source without a key in config raises ValueError."""
    config = _sa_key_config(FAKE_SA_KEY)
    del config["gcp_service_account_key"]

    with pytest.raises(ValueError, match="service_account_key"):
        await GCPCredentialInjector.build_env(config=config)


# ---------------------------------------------------------------------------
# SA hardening: vm_default + impersonation via gcp_bootstrap_sa_email
# ---------------------------------------------------------------------------


def test_load_gcp_credentials_vm_default_uses_bootstrap_sa_email():
    """vm_default + gcp_bootstrap_sa_email returns impersonated creds targeting bootstrap."""
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_bootstrap_sa_email": "bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    }
    fake_source = MagicMock(name="adc_source_credentials")
    with (
        patch(
            "app.services.credential_injector._google_auth.default",
            return_value=(fake_source, "my-project"),
        ),
        patch("app.services.credential_injector._impersonated_credentials.Credentials") as imp_cls,
    ):
        load_gcp_credentials(config)
        imp_cls.assert_called_once()
        kwargs = imp_cls.call_args.kwargs
        assert kwargs["target_principal"] == "bioaf-bootstrap@my-project.iam.gserviceaccount.com"
        assert kwargs["source_credentials"] is fake_source


def test_load_gcp_credentials_vm_default_falls_back_to_service_account_email():
    """vm_default + only legacy gcp_service_account_email -> uses legacy as impersonation target."""
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_service_account_email": "legacy-sa@my-project.iam.gserviceaccount.com",
    }
    fake_source = MagicMock(name="adc_source_credentials")
    with (
        patch(
            "app.services.credential_injector._google_auth.default",
            return_value=(fake_source, "my-project"),
        ),
        patch("app.services.credential_injector._impersonated_credentials.Credentials") as imp_cls,
    ):
        load_gcp_credentials(config)
        imp_cls.assert_called_once()
        assert imp_cls.call_args.kwargs["target_principal"] == "legacy-sa@my-project.iam.gserviceaccount.com"


def test_load_gcp_credentials_vm_default_prefers_bootstrap_over_legacy():
    """When both keys are present, the new gcp_bootstrap_sa_email wins."""
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_bootstrap_sa_email": "bioaf-bootstrap@my-project.iam.gserviceaccount.com",
        "gcp_service_account_email": "legacy-sa@my-project.iam.gserviceaccount.com",
    }
    fake_source = MagicMock(name="adc_source_credentials")
    with (
        patch(
            "app.services.credential_injector._google_auth.default",
            return_value=(fake_source, "my-project"),
        ),
        patch("app.services.credential_injector._impersonated_credentials.Credentials") as imp_cls,
    ):
        load_gcp_credentials(config)
        assert imp_cls.call_args.kwargs["target_principal"] == "bioaf-bootstrap@my-project.iam.gserviceaccount.com"


def test_load_gcp_credentials_vm_default_no_emails_returns_raw_adc():
    """vm_default with neither field -> returns raw ADC creds (no impersonation)."""
    config = {"gcp_credential_source": "vm_default"}
    fake_source = MagicMock(name="adc_source_credentials")
    with (
        patch(
            "app.services.credential_injector._google_auth.default",
            return_value=(fake_source, "my-project"),
        ),
        patch("app.services.credential_injector._impersonated_credentials.Credentials") as imp_cls,
    ):
        result = load_gcp_credentials(config)
        imp_cls.assert_not_called()
        assert result is fake_source


@pytest.mark.asyncio
async def test_build_env_vm_default_does_not_set_credentials_file():
    """build_env in vm_default mode never writes a key file even with impersonation set."""
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
        "gcp_zone": "us-central1-a",
        "gcp_bootstrap_sa_email": "bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    }
    env, cleanup = await GCPCredentialInjector.build_env(config=config)
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    await cleanup()


@pytest.mark.asyncio
async def test_build_env_vm_default_sets_impersonation_env_var_for_terraform():
    """build_env must set GOOGLE_IMPERSONATE_SERVICE_ACCOUNT in vm_default mode
    when a bootstrap SA email is configured. Terraform's google provider reads
    this variable to impersonate the target SA -- without it, terraform apply
    runs as the VM's attached identity (bioaf-app) and lacks the broad
    permissions required for pubsub/iam/build operations.
    """
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
        "gcp_zone": "us-central1-a",
        "gcp_bootstrap_sa_email": "bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    }
    env, cleanup = await GCPCredentialInjector.build_env(config=config)
    assert env.get("GOOGLE_IMPERSONATE_SERVICE_ACCOUNT") == "bioaf-bootstrap@my-project.iam.gserviceaccount.com"
    await cleanup()


@pytest.mark.asyncio
async def test_build_env_vm_default_falls_back_to_legacy_email_for_impersonation():
    """build_env honours the legacy gcp_service_account_email when no
    bootstrap email is set, matching load_gcp_credentials' precedence."""
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
        "gcp_zone": "us-central1-a",
        "gcp_service_account_email": "legacy@my-project.iam.gserviceaccount.com",
    }
    env, cleanup = await GCPCredentialInjector.build_env(config=config)
    assert env.get("GOOGLE_IMPERSONATE_SERVICE_ACCOUNT") == "legacy@my-project.iam.gserviceaccount.com"
    await cleanup()


@pytest.mark.asyncio
async def test_build_env_vm_default_no_impersonation_when_no_email():
    """No impersonation env var when neither key is set -- terraform runs as
    the VM-attached identity (bioaf-app) directly, which is the right
    behaviour for legacy installs that never set a bootstrap email."""
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
        "gcp_zone": "us-central1-a",
    }
    env, cleanup = await GCPCredentialInjector.build_env(config=config)
    assert "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT" not in env
    await cleanup()
