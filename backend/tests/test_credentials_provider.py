"""Tests for the backend-aware Credentials provider seam (Stage 3c).

The seam (`app/adapters/credentials/`) is the single place credential resolution
lives on the adapter side of the BAL. Its GCP implementation
(`GcpCredentialsProvider`) currently forwards to the existing
`platform.credential_injector` (so existing mocks that patch the injector keep
working while the service-layer leaks drain), and adds the two shapes the seam
must expose that the injector did not: a bearer-token minter and a
permission-denied classifier. AWS (STS / assume-role / instance profile) slots in
behind the same interface later.

All local-runnable (no DB): the google-auth calls are patched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.credentials import (
    DEFAULT_CREDENTIALS_BACKEND,
    VALID_CREDENTIALS_BACKENDS,
    create_credentials_provider,
)
from app.adapters.credentials.base import CredentialsProvider
from app.adapters.credentials.gcp import GcpCredentialsProvider
from app.exceptions import ValidationError

_GCP_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

FAKE_SA_KEY = json.dumps(
    {
        "type": "service_account",
        "project_id": "my-project",
        "client_email": "bioaf@my-project.iam.gserviceaccount.com",
    }
)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_defaults_to_gcp():
    assert DEFAULT_CREDENTIALS_BACKEND == "gcp"
    assert "gcp" in VALID_CREDENTIALS_BACKENDS
    provider = create_credentials_provider()
    assert isinstance(provider, GcpCredentialsProvider)
    assert isinstance(provider, CredentialsProvider)


def test_factory_rejects_unknown_backend():
    with pytest.raises(ValidationError, match="credentials backend"):
        create_credentials_provider(backend="azure")


# ---------------------------------------------------------------------------
# load_credentials: forwards to the injector, byte-for-byte behavior-preserving
# ---------------------------------------------------------------------------


def test_load_credentials_default_path_forwards_to_injector():
    """No overrides -> the injector's standard resolution (default impersonation)."""
    provider = GcpCredentialsProvider()
    sentinel = MagicMock(name="creds")
    with patch(
        "app.adapters.credentials.gcp.credential_injector.load_gcp_credentials",
        return_value=sentinel,
    ) as load:
        result = provider.load_credentials({"gcp_credential_source": "vm_default"})
    assert result is sentinel
    load.assert_called_once()
    # Default call must not force impersonate_target (lets the injector apply its
    # own config-derived default), so the standard path is unchanged.
    assert "impersonate_target" not in load.call_args.kwargs


def test_load_credentials_no_impersonation_passes_explicit_none():
    """impersonate_target=None must reach the injector as an explicit None."""
    provider = GcpCredentialsProvider()
    with patch(
        "app.adapters.credentials.gcp.credential_injector.load_gcp_credentials",
        return_value=MagicMock(),
    ) as load:
        provider.load_credentials({"gcp_credential_source": "vm_default"}, impersonate_target=None)
    assert load.call_args.kwargs["impersonate_target"] is None


def test_load_credentials_explicit_target_and_scopes_and_lifetime():
    """The reader-SA shape: explicit target, custom scopes, lifetime, key override."""
    provider = GcpCredentialsProvider()
    with patch(
        "app.adapters.credentials.gcp.credential_injector.load_gcp_credentials",
        return_value=MagicMock(),
    ) as load:
        provider.load_credentials(
            {"gcp_credential_source": "vm_default"},
            scopes=_SHEETS_SCOPE,
            impersonate_target="reader@my-project.iam.gserviceaccount.com",
            key_json="{}",
            lifetime=3600,
        )
    kwargs = load.call_args.kwargs
    assert kwargs["scopes"] == _SHEETS_SCOPE
    assert kwargs["impersonate_target"] == "reader@my-project.iam.gserviceaccount.com"
    assert kwargs["key_json"] == "{}"
    assert kwargs["lifetime"] == 3600


# ---------------------------------------------------------------------------
# bearer_token: refresh + extract, the shape the image services need
# ---------------------------------------------------------------------------


def test_bearer_token_refreshes_and_returns_token():
    provider = GcpCredentialsProvider()
    creds = MagicMock()
    creds.token = "ya29.fake-access-token"
    token = provider.bearer_token(creds)
    assert token == "ya29.fake-access-token"
    creds.refresh.assert_called_once()


# ---------------------------------------------------------------------------
# build_subprocess_env: the Terraform shape (forwards to the injector)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_subprocess_env_vm_default():
    provider = GcpCredentialsProvider()
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
        "gcp_zone": "us-central1-a",
    }
    env, cleanup = await provider.build_subprocess_env(config)
    assert env["TF_VAR_project_id"] == "my-project"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    await cleanup()


@pytest.mark.asyncio
async def test_build_subprocess_env_sa_key_writes_and_cleans_up():
    provider = GcpCredentialsProvider()
    config = {
        "gcp_credential_source": "service_account_key",
        "gcp_project_id": "my-project",
        "gcp_region": "us-central1",
        "gcp_zone": "us-central1-a",
        "gcp_service_account_key": FAKE_SA_KEY,
    }
    env, cleanup = await provider.build_subprocess_env(config)
    key_path = Path(env["GOOGLE_APPLICATION_CREDENTIALS"])
    assert key_path.exists()
    await cleanup()
    assert not key_path.exists()


# ---------------------------------------------------------------------------
# is_permission_denied: the exception shape billing_export needs
# ---------------------------------------------------------------------------


def test_is_permission_denied_true_for_forbidden():
    from google.api_core.exceptions import Forbidden

    provider = GcpCredentialsProvider()
    assert provider.is_permission_denied(Forbidden("denied")) is True


def test_is_permission_denied_false_for_other_errors():
    provider = GcpCredentialsProvider()
    assert provider.is_permission_denied(ValueError("boom")) is False
    assert provider.is_permission_denied(RuntimeError("nope")) is False
