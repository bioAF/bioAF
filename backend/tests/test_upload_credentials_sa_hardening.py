"""Tests that upload signed-URL generation works under SA hardening.

Pre-fix bug: upload_service._get_gcs_credentials returned None on vm_default
installs, falling through to raw ADC. Raw ADC has no private signer, so
blob.generate_signed_url raised "you need a private key" and surfaced as
'Unknown Error' in the UI. The fix routes through credential_injector so
vm_default installs get impersonated bootstrap credentials, which sign via
the IAM SignBlob API.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text


async def _seed_vm_default(session) -> None:
    await session.execute(
        text(
            """
            INSERT INTO platform_config (key, value) VALUES
                ('gcp_credential_source', 'vm_default'),
                ('gcp_project_id', 'my-project'),
                ('gcp_bootstrap_sa_email', 'bioaf-bootstrap@my-project.iam.gserviceaccount.com')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_get_gcs_credentials_returns_impersonated_creds_in_vm_default(session):
    from app.services.upload_service import UploadService

    await _seed_vm_default(session)
    fake_creds = MagicMock(name="impersonated_creds")
    with patch("app.services.credential_injector.load_gcp_credentials", return_value=fake_creds) as load:
        result = await UploadService._get_gcs_credentials(session)
    load.assert_called_once()
    config_arg = load.call_args.args[0]
    assert config_arg["gcp_credential_source"] == "vm_default"
    assert config_arg["gcp_bootstrap_sa_email"] == "bioaf-bootstrap@my-project.iam.gserviceaccount.com"
    assert result is fake_creds


@pytest.mark.asyncio
async def test_get_gcs_credentials_returns_none_when_injector_fails(session):
    """Failure must not raise -- caller falls back gracefully."""
    from app.services.upload_service import UploadService

    await _seed_vm_default(session)
    with patch(
        "app.services.credential_injector.load_gcp_credentials",
        side_effect=RuntimeError("creds unavailable"),
    ):
        result = await UploadService._get_gcs_credentials(session)
    assert result is None


@pytest.mark.asyncio
async def test_storage_service_query_gcs_buckets_uses_impersonated_creds(session):
    """_query_gcs_buckets must pass impersonated bootstrap creds so list_buckets
    is authorized at the project level (bioaf-app's storage.admin is conditioned
    on bucket name and never matches at the project resource).
    """
    from app.services.storage_service import StorageService

    await _seed_vm_default(session)

    fake_creds = MagicMock(name="impersonated_creds")
    fake_storage_client = MagicMock()
    fake_storage_client.list_buckets.return_value = []  # empty result is fine

    with (
        patch("app.services.credential_injector.load_gcp_credentials", return_value=fake_creds) as load,
        patch("google.cloud.storage.Client", return_value=fake_storage_client) as gcs_client_cls,
    ):
        await StorageService._query_gcs_buckets(session, org_id=1)

    load.assert_called_once()
    # Storage client must have been constructed with the impersonated creds
    assert any(call.kwargs.get("credentials") is fake_creds for call in gcs_client_cls.call_args_list), (
        "gcs_storage.Client should be called with credentials=fake_creds"
    )


@pytest.mark.asyncio
async def test_storage_service_falls_back_when_credentials_unavailable(session):
    """Bare gcs_storage.Client() is used when credential_injector returns None."""
    from app.services.storage_service import StorageService

    # No GCP config seeded -- credential_injector will raise.
    fake_storage_client = MagicMock()
    fake_storage_client.list_buckets.return_value = []

    with (
        patch(
            "app.services.credential_injector.load_gcp_credentials",
            side_effect=RuntimeError("no config"),
        ),
        patch("google.cloud.storage.Client", return_value=fake_storage_client) as gcs_client_cls,
    ):
        await StorageService._query_gcs_buckets(session, org_id=1)

    # Called without credentials kwarg
    last_call = gcs_client_cls.call_args_list[-1]
    assert "credentials" not in last_call.kwargs


@pytest.mark.asyncio
async def test_initiate_upload_uses_signing_credentials(session):
    """End-to-end: initiate_upload pulls credentials and passes them to
    generate_signed_upload_url so v4 signing works on vm_default installs.
    """
    from app.services.upload_service import UploadService

    await _seed_vm_default(session)
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES ('ingest_bucket_name', 'bioaf-1-ingest') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )
    await session.commit()

    fake_creds = MagicMock(name="impersonated_creds")

    with (
        patch("app.services.credential_injector.load_gcp_credentials", return_value=fake_creds),
        patch.object(
            UploadService,
            "_generate_signed_upload_url",
            new=AsyncMock(return_value="https://signed.example/?sig=…"),
        ) as gen,
    ):
        result = await UploadService.initiate_upload(
            session,
            org_id=1,
            user_id=1,
            filename="test.csv",
            expected_size=10,
            expected_md5=None,
            experiment_id=None,
        )

    assert result["signed_url"] == "https://signed.example/?sig=…"
    # The credentials passed to the signer are the ones from credential_injector
    assert gen.call_args.kwargs["credentials"] is fake_creds
