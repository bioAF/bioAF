"""IamProvider BAL seam (Phase 9B).

The IAM provider is a platform-service provider (like secrets/messaging/logging),
selected by a config-keyed factory rather than the DB-backed registry. It drains
the ``google.cloud.iam_admin_v1`` import out of
``services/orphaned_resource_service.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.exceptions import ValidationError

from app.adapters.iam import (
    DEFAULT_IAM_BACKEND,
    VALID_IAM_BACKENDS,
    create_iam_provider,
)
from app.adapters.iam.base import IamProvider, ServiceAccount


def test_factory_returns_gcp_provider_by_default():
    provider = create_iam_provider()
    assert isinstance(provider, IamProvider)
    assert DEFAULT_IAM_BACKEND == "gcp"
    assert "gcp" in VALID_IAM_BACKENDS


def test_factory_rejects_unknown_backend():
    with pytest.raises(ValidationError, match="Unknown IAM backend"):
        create_iam_provider(backend="azure")


def test_factory_passes_credentials_through():
    creds = MagicMock()
    with patch("app.adapters.iam.gcp.GcpIamProvider") as ctor:
        create_iam_provider(credentials=creds)
        ctor.assert_called_once_with(credentials=creds)


def test_list_service_accounts_normalizes_to_service_account_models():
    """The raw SDK SA objects are mapped to ServiceAccount(email, account_id)."""
    from app.adapters.iam.gcp import GcpIamProvider

    sa1 = MagicMock(email="bioaf-notebook-runner@proj.iam.gserviceaccount.com")
    sa2 = MagicMock(email="other-sa@proj.iam.gserviceaccount.com")
    client = MagicMock()
    client.list_service_accounts.return_value = [sa1, sa2]

    with patch("google.cloud.iam_admin_v1.IAMClient", return_value=client):
        provider = GcpIamProvider(credentials=None)
        result = provider.list_service_accounts("proj")

    client.list_service_accounts.assert_called_once_with(name="projects/proj")
    assert result == [
        ServiceAccount(email="bioaf-notebook-runner@proj.iam.gserviceaccount.com", account_id="bioaf-notebook-runner"),
        ServiceAccount(email="other-sa@proj.iam.gserviceaccount.com", account_id="other-sa"),
    ]


def test_list_service_accounts_uses_credentials_when_present():
    creds = MagicMock()
    client = MagicMock()
    client.list_service_accounts.return_value = []

    with patch("google.cloud.iam_admin_v1.IAMClient", return_value=client) as ctor:
        from app.adapters.iam.gcp import GcpIamProvider

        GcpIamProvider(credentials=creds).list_service_accounts("proj")
        ctor.assert_called_once_with(credentials=creds)


def test_delete_service_account_builds_full_resource_name():
    """The GCP SA resource-name format stays inside the adapter."""
    from app.adapters.iam.gcp import GcpIamProvider

    client = MagicMock()
    with patch("google.cloud.iam_admin_v1.IAMClient", return_value=client):
        GcpIamProvider(credentials=None).delete_service_account("proj", "bioaf-notebook-runner")

    client.delete_service_account.assert_called_once_with(
        name="projects/proj/serviceAccounts/bioaf-notebook-runner@proj.iam.gserviceaccount.com"
    )


def test_delete_service_account_without_credentials_uses_adc():
    client = MagicMock()
    with patch("google.cloud.iam_admin_v1.IAMClient", return_value=client) as ctor:
        from app.adapters.iam.gcp import GcpIamProvider

        GcpIamProvider(credentials=None).delete_service_account("proj", "sa")
        ctor.assert_called_once_with()
