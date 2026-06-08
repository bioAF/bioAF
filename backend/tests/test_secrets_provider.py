"""Phase 9C: Secret Manager access goes through a BAL SecretsProvider.

Secrets are fetched at startup (the DB password is among them), before the DB and
the registry are up, so this provider is a bootstrap concern selected by config
rather than a DB-resolved registry category. The seam moves the
google.cloud.secretmanager import out of services/ into adapters/ and lets a
non-GCP secrets backend (AWS Secrets Manager, Vault) slot in later.
"""

from unittest.mock import MagicMock, patch

from app.adapters.secrets import create_secrets_provider
from app.adapters.secrets.base import SecretsProvider
from app.adapters.secrets.gcp import GcpSecretsProvider


def test_factory_returns_gcp_provider_by_default():
    provider = create_secrets_provider("my-proj")
    assert isinstance(provider, GcpSecretsProvider)
    assert isinstance(provider, SecretsProvider)


def test_unknown_secrets_backend_raises():
    import pytest

    with pytest.raises(ValueError):
        create_secrets_provider("my-proj", backend="vault")


def test_gcp_access_secret_reads_latest_version():
    fake_client = MagicMock()
    fake_client.access_secret_version.return_value.payload.data = b"s3cr3t-value"
    with patch(
        "google.cloud.secretmanager.SecretManagerServiceClient",
        return_value=fake_client,
    ):
        provider = GcpSecretsProvider("my-proj")
        value = provider.access_secret("bioaf-jwt-signing-key")

    assert value == "s3cr3t-value"
    request = fake_client.access_secret_version.call_args.kwargs["request"]
    assert request["name"] == "projects/my-proj/secrets/bioaf-jwt-signing-key/versions/latest"


def test_secrets_service_uses_provider_and_tolerates_per_entry_failure():
    from app.services.secrets_service import SecretsService

    svc = SecretsService("my-proj")

    def fake_access(name: str) -> str:
        if name == "bioaf-db-app-password":
            raise RuntimeError("boom")
        return f"val-{name}"

    svc._provider = MagicMock()
    svc._provider.access_secret.side_effect = fake_access

    result = svc.fetch_all()

    # The failed entry is skipped, the rest load (preserves prior behavior).
    assert "bioaf-db-app-password" not in result
    assert result["bioaf-jwt-signing-key"] == "val-bioaf-jwt-signing-key"
    assert svc.get_secret("bioaf-jwt-signing-key") == "val-bioaf-jwt-signing-key"


def test_secrets_service_raises_when_nothing_loads():
    import pytest

    from app.services.secrets_service import SecretsService

    svc = SecretsService("my-proj")
    svc._provider = MagicMock()
    svc._provider.access_secret.side_effect = RuntimeError("all down")

    with pytest.raises(RuntimeError):
        svc.fetch_all()
