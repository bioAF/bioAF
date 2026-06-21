"""Tests for image-build credential delegation (Breakage 7).

notebook_image_service._get_credentials and cellxgene_image_service._get_credentials
must route through credential_injector.load_gcp_credentials so that
gcp_bootstrap_sa_email impersonation is honored. Cloud Build and Artifact
Registry require permissions only bioaf-bootstrap holds.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from app.services import notebook_image_service


async def _seed(session, **kv: str) -> None:
    for k, v in kv.items():
        await session.execute(
            text(
                "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ).bindparams(k=k, v=v)
        )
    await session.commit()


@pytest.mark.asyncio
async def test_notebook_image_get_credentials_uses_injector(session):
    """_get_credentials delegates to credential_injector with the bootstrap key."""
    await _seed(
        session,
        gcp_credential_source="vm_default",
        gcp_bootstrap_sa_email="bioaf-bootstrap@test.iam.gserviceaccount.com",
    )

    sentinel = MagicMock(name="impersonated_creds")
    with patch(
        "app.services.notebook_image_service.load_gcp_credentials",
        return_value=sentinel,
    ) as mock_loader:
        result = await notebook_image_service._get_credentials(session)

    assert result is sentinel
    mock_loader.assert_called_once()
    call_config = mock_loader.call_args.args[0]
    assert call_config["gcp_credential_source"] == "vm_default"
    assert call_config["gcp_bootstrap_sa_email"] == "bioaf-bootstrap@test.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_notebook_image_get_credentials_passes_legacy_fallback(session):
    """When only the legacy email is set, _get_credentials passes it through."""
    await _seed(
        session,
        gcp_credential_source="vm_default",
        gcp_service_account_email="legacy@test.iam.gserviceaccount.com",
    )
    sentinel = MagicMock()
    with patch(
        "app.services.notebook_image_service.load_gcp_credentials",
        return_value=sentinel,
    ) as mock_loader:
        await notebook_image_service._get_credentials(session)
    cfg = mock_loader.call_args.args[0]
    assert cfg.get("gcp_service_account_email") == "legacy@test.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_cellxgene_image_resolves_credentials_via_shared_helper(session):
    """cellxgene resolves credentials through the shared image-platform resolver,
    which on GCP delegates to the notebook bootstrap-impersonation helper rather
    than duplicating it (the cellxgene/notebook build paths both reach it)."""
    from app.services.image_build_platform import ImagePlatform, resolve_image_credentials

    sentinel = MagicMock(name="impersonated_creds")
    gcp = ImagePlatform("gcp", {"project_id": "p", "region": "r"}, None)
    with patch(
        "app.services.notebook_image_service._get_credentials",
        AsyncMock(return_value=sentinel),
    ) as mock_helper:
        result = await resolve_image_credentials(session, gcp)
    assert result is sentinel
    mock_helper.assert_awaited_once()


@pytest.mark.asyncio
async def test_environment_build_inherits_credentials_fix(session):
    """environment_build_service imports _get_credentials from notebook_image_service."""
    from app.services import environment_build_service

    assert environment_build_service._get_credentials is notebook_image_service._get_credentials
