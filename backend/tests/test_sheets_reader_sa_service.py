"""Tests for sheets_reader_sa_service credential loading and SA hardening.

Covers:
- _load_primary_credentials reads gcp_bootstrap_sa_email first, falls back to
  the legacy gcp_service_account_email.
- get_reader_credentials returns impersonated credentials targeting the
  reader SA when running in vm_default mode (no JSON key required).
- get_reader_credentials falls back to the stored JSON key only on legacy
  service_account_key installs.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import sheets_reader_sa_service


def test_load_primary_credentials_delegates_to_seam():
    """_load_primary_credentials asks the Credentials seam for the primary SA
    creds (default cloud-platform scope + config-derived impersonation) and
    returns them with the project id. Impersonation precedence (bootstrap over
    legacy) now lives in the seam and is tested in test_credential_injector."""
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_bootstrap_sa_email": "bioaf-bootstrap@my-project.iam.gserviceaccount.com",
    }
    sentinel = MagicMock(name="primary_creds")
    provider = MagicMock()
    provider.load_credentials.return_value = sentinel
    with patch.object(sheets_reader_sa_service, "get_credentials_provider", return_value=provider):
        creds, project = sheets_reader_sa_service._load_primary_credentials(config)
    assert creds is sentinel
    assert project == "my-project"
    provider.load_credentials.assert_called_once_with(config)


def test_load_primary_credentials_raises_without_project():
    provider = MagicMock()
    with patch.object(sheets_reader_sa_service, "get_credentials_provider", return_value=provider):
        with pytest.raises(RuntimeError, match="project ID"):
            sheets_reader_sa_service._load_primary_credentials({"gcp_credential_source": "vm_default"})
    provider.load_credentials.assert_not_called()


def test_load_primary_credentials_raises_when_sa_key_missing():
    """service_account_key source but no key configured fails before the seam."""
    config = {"gcp_credential_source": "service_account_key", "gcp_project_id": "my-project"}
    provider = MagicMock()
    with patch.object(sheets_reader_sa_service, "get_credentials_provider", return_value=provider):
        with pytest.raises(RuntimeError, match="service account key"):
            sheets_reader_sa_service._load_primary_credentials(config)
    provider.load_credentials.assert_not_called()


def test_gcp_keys_constant_includes_bootstrap_sa_email():
    """The list of keys SELECTed by sheets reader includes the new bootstrap field."""
    assert "gcp_bootstrap_sa_email" in sheets_reader_sa_service._GCP_KEYS
    assert "gcp_service_account_email" in sheets_reader_sa_service._GCP_KEYS


@pytest.mark.asyncio
async def test_get_reader_credentials_impersonates_in_vm_default_mode():
    """vm_default install: asks the seam to impersonate the reader SA with a
    short-lived Sheets-scoped token, no key needed."""
    session = MagicMock()
    config_rows = {
        "sheets_reader_sa_email": "bioaf-reader@my-project.iam.gserviceaccount.com",
        "sheets_reader_sa_created": "true",
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
    }

    async def fake_read_keys(_session, _keys):
        return config_rows

    sentinel = MagicMock(name="reader_creds")
    provider = MagicMock()
    provider.load_credentials.return_value = sentinel
    with (
        patch.object(sheets_reader_sa_service, "_read_keys", side_effect=fake_read_keys),
        patch.object(sheets_reader_sa_service, "get_credentials_provider", return_value=provider),
    ):
        result = await sheets_reader_sa_service.get_reader_credentials(session)
    assert result is sentinel
    provider.load_credentials.assert_called_once_with(
        config_rows,
        scopes=sheets_reader_sa_service._SHEETS_SCOPE,
        impersonate_target="bioaf-reader@my-project.iam.gserviceaccount.com",
        lifetime=3600,
    )


@pytest.mark.asyncio
async def test_get_reader_credentials_uses_stored_key_for_legacy_installs():
    """service_account_key (legacy) install: asks the seam to build creds from the
    reader SA's own stored JSON key, scoped to Sheets readonly."""
    session = MagicMock()
    reader_key = json.dumps(
        {
            "type": "service_account",
            "project_id": "my-project",
            "client_email": "bioaf-reader@my-project.iam.gserviceaccount.com",
        }
    )
    config_rows = {
        "sheets_reader_sa_email": "bioaf-reader@my-project.iam.gserviceaccount.com",
        "sheets_reader_sa_created": "true",
        "sheets_reader_sa_key": reader_key,
        "gcp_credential_source": "service_account_key",
    }

    async def fake_read_keys(_session, _keys):
        return config_rows

    provider = MagicMock()
    with (
        patch.object(sheets_reader_sa_service, "_read_keys", side_effect=fake_read_keys),
        patch.object(sheets_reader_sa_service, "get_credentials_provider", return_value=provider),
    ):
        await sheets_reader_sa_service.get_reader_credentials(session)
    provider.load_credentials.assert_called_once_with(
        config_rows,
        scopes=sheets_reader_sa_service._SHEETS_SCOPE,
        key_json=reader_key,
    )


@pytest.mark.asyncio
async def test_get_reader_credentials_raises_when_not_configured():
    session = MagicMock()

    async def fake_read_keys(_session, _keys):
        return {}

    with patch.object(sheets_reader_sa_service, "_read_keys", side_effect=fake_read_keys):
        with pytest.raises(RuntimeError, match="not configured"):
            await sheets_reader_sa_service.get_reader_credentials(session)


@pytest.mark.asyncio
async def test_create_reader_sa_skips_keys_create_and_grants_token_creator():
    """create_reader_sa creates the SA, binds tokenCreator for the runtime SA, no JSON key."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    async def fake_read_keys(_session, keys):
        if "gcp_project_id" in keys:
            return {
                "gcp_project_id": "my-project",
                "gcp_credential_source": "vm_default",
                "gcp_bootstrap_sa_email": "bioaf-bootstrap@my-project.iam.gserviceaccount.com",
            }
        return {}  # status check -> not yet created

    fake_creds = MagicMock()
    new_sa_email = "bioaf-reader-abcd1234@my-project.iam.gserviceaccount.com"

    iam_chain = MagicMock()
    iam_chain.projects().serviceAccounts().create().execute.return_value = {"email": new_sa_email}
    iam_chain.projects().serviceAccounts().getIamPolicy().execute.return_value = {"bindings": []}
    set_iam_policy = iam_chain.projects().serviceAccounts().setIamPolicy
    set_iam_policy().execute.return_value = {}

    service_usage = MagicMock()
    service_usage.services().enable().execute.return_value = {}

    def fake_discovery_build(api, _ver, **_kwargs):
        return iam_chain if api == "iam" else service_usage

    with (
        patch.object(sheets_reader_sa_service, "_read_keys", side_effect=fake_read_keys),
        patch.object(sheets_reader_sa_service, "_load_primary_credentials", return_value=(fake_creds, "my-project")),
        patch.object(sheets_reader_sa_service, "discovery_build", side_effect=fake_discovery_build),
        patch(
            "app.services.bootstrap_metadata.get_attached_sa_email",
            new=AsyncMock(return_value="bioaf-app@my-project.iam.gserviceaccount.com"),
        ),
        patch.object(sheets_reader_sa_service, "_upsert", new=AsyncMock()) as upsert,
    ):
        result = await sheets_reader_sa_service.create_reader_sa(session)

    assert result["email"] == new_sa_email

    # No key was ever upserted
    upserted_keys = [c.args[1] for c in upsert.await_args_list]
    assert "sheets_reader_sa_key" not in upserted_keys
    assert "sheets_reader_sa_email" in upserted_keys
    assert "sheets_reader_sa_created" in upserted_keys

    # tokenCreator binding includes the runtime SA
    set_call_kwargs = set_iam_policy.call_args.kwargs
    bindings = set_call_kwargs["body"]["policy"]["bindings"]
    token_creator = next(b for b in bindings if b["role"] == "roles/iam.serviceAccountTokenCreator")
    assert "serviceAccount:bioaf-app@my-project.iam.gserviceaccount.com" in token_creator["members"]


@pytest.mark.asyncio
async def test_create_reader_sa_fails_clearly_when_runtime_email_unknown():
    """Off-GCE / metadata server unreachable: surface an actionable error."""
    session = MagicMock()

    async def fake_read_keys(_session, keys):
        if "gcp_project_id" in keys:
            return {"gcp_project_id": "my-project", "gcp_credential_source": "vm_default"}
        return {}

    with (
        patch.object(sheets_reader_sa_service, "_read_keys", side_effect=fake_read_keys),
        patch.object(sheets_reader_sa_service, "_load_primary_credentials", return_value=(MagicMock(), "my-project")),
        patch(
            "app.services.bootstrap_metadata.get_attached_sa_email",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(RuntimeError, match="runtime service account"):
            await sheets_reader_sa_service.create_reader_sa(session)
