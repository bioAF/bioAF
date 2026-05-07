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


def test_load_primary_credentials_prefers_bootstrap_sa_email():
    """vm_default + gcp_bootstrap_sa_email targets the new key."""
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_bootstrap_sa_email": "bioaf-bootstrap@my-project.iam.gserviceaccount.com",
        "gcp_service_account_email": "legacy-sa@my-project.iam.gserviceaccount.com",
    }
    fake_source = MagicMock(name="adc")
    with (
        patch("google.auth.default", return_value=(fake_source, "my-project")),
        patch("google.auth.impersonated_credentials.Credentials") as imp_cls,
    ):
        sheets_reader_sa_service._load_primary_credentials(config)
        imp_cls.assert_called_once()
        assert imp_cls.call_args.kwargs["target_principal"] == "bioaf-bootstrap@my-project.iam.gserviceaccount.com"


def test_load_primary_credentials_falls_back_to_service_account_email():
    """Existing installs without the new key still impersonate via legacy field."""
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
        "gcp_service_account_email": "legacy-sa@my-project.iam.gserviceaccount.com",
    }
    fake_source = MagicMock(name="adc")
    with (
        patch("google.auth.default", return_value=(fake_source, "my-project")),
        patch("google.auth.impersonated_credentials.Credentials") as imp_cls,
    ):
        sheets_reader_sa_service._load_primary_credentials(config)
        assert imp_cls.call_args.kwargs["target_principal"] == "legacy-sa@my-project.iam.gserviceaccount.com"


def test_load_primary_credentials_raw_adc_when_no_email():
    config = {
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
    }
    fake_source = MagicMock(name="adc")
    with (
        patch("google.auth.default", return_value=(fake_source, "my-project")),
        patch("google.auth.impersonated_credentials.Credentials") as imp_cls,
    ):
        creds, project = sheets_reader_sa_service._load_primary_credentials(config)
        imp_cls.assert_not_called()
        assert creds is fake_source
        assert project == "my-project"


def test_gcp_keys_constant_includes_bootstrap_sa_email():
    """The list of keys SELECTed by sheets reader includes the new bootstrap field."""
    assert "gcp_bootstrap_sa_email" in sheets_reader_sa_service._GCP_KEYS
    assert "gcp_service_account_email" in sheets_reader_sa_service._GCP_KEYS


@pytest.mark.asyncio
async def test_get_reader_credentials_impersonates_in_vm_default_mode():
    """vm_default install: returns impersonated creds targeting the reader SA, no key needed."""
    session = MagicMock()
    config_rows = {
        "sheets_reader_sa_email": "bioaf-reader@my-project.iam.gserviceaccount.com",
        "sheets_reader_sa_created": "true",
        "gcp_credential_source": "vm_default",
        "gcp_project_id": "my-project",
    }

    async def fake_read_keys(_session, _keys):
        return config_rows

    fake_source = MagicMock(name="adc")
    with (
        patch.object(sheets_reader_sa_service, "_read_keys", side_effect=fake_read_keys),
        patch("google.auth.default", return_value=(fake_source, "my-project")),
        patch.object(sheets_reader_sa_service, "impersonated_credentials") as imp_module,
    ):
        await sheets_reader_sa_service.get_reader_credentials(session)
        imp_module.Credentials.assert_called_once()
        kwargs = imp_module.Credentials.call_args.kwargs
        assert kwargs["target_principal"] == "bioaf-reader@my-project.iam.gserviceaccount.com"
        assert kwargs["target_scopes"] == sheets_reader_sa_service._SHEETS_SCOPE
        assert kwargs["source_credentials"] is fake_source


@pytest.mark.asyncio
async def test_get_reader_credentials_uses_stored_key_for_legacy_installs():
    """service_account_key (legacy) install: still loads the JSON key from platform_config."""
    session = MagicMock()
    fake_key = {
        "type": "service_account",
        "project_id": "my-project",
        "client_email": "bioaf-reader@my-project.iam.gserviceaccount.com",
    }
    config_rows = {
        "sheets_reader_sa_email": "bioaf-reader@my-project.iam.gserviceaccount.com",
        "sheets_reader_sa_created": "true",
        "sheets_reader_sa_key": json.dumps(fake_key),
        "gcp_credential_source": "service_account_key",
    }

    async def fake_read_keys(_session, _keys):
        return config_rows

    with (
        patch.object(sheets_reader_sa_service, "_read_keys", side_effect=fake_read_keys),
        patch.object(sheets_reader_sa_service.service_account.Credentials, "from_service_account_info") as mk,
    ):
        await sheets_reader_sa_service.get_reader_credentials(session)
        mk.assert_called_once_with(fake_key, scopes=sheets_reader_sa_service._SHEETS_SCOPE)


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
