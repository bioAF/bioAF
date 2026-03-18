"""Tests for the non-streaming background deploy endpoint.

This endpoint starts a deploy in the background and returns immediately
with a run_id, enabling the setup wizard to kick off deployment without
maintaining an SSE connection.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import text


async def _set_config(session, key: str, value: str):
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(k=key, v=value)
    )
    await session.flush()


class TestStackDeployBackground:
    @pytest.mark.asyncio
    async def test_requires_admin(self, client, viewer_token, session):
        """Non-admin users cannot trigger background deploy."""
        response = await client.post(
            "/api/v1/infrastructure/stack/deploy-background",
            json={"stack_type": "kubernetes"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_run_id(self, client, admin_token, session):
        """Background deploy returns a run_id and starts immediately."""
        await _set_config(session, "gcp_credentials_configured", "true")
        await _set_config(session, "terraform_initialized", "true")
        await _set_config(session, "compute_deployed", "false")
        await _set_config(session, "storage_deployed", "true")
        await session.commit()

        from app.services.terraform_executor import TerraformProgressEvent

        async def mock_deploy(sess, stack_type, user_id, org_id=None):
            yield TerraformProgressEvent(event_type="stack_complete", message="done")

        with patch("app.api.stack_deploy.deploy_stack", side_effect=mock_deploy):
            response = await client.post(
                "/api/v1/infrastructure/stack/deploy-background",
                json={"stack_type": "kubernetes"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert data["message"] == "Deployment started"

    @pytest.mark.asyncio
    async def test_rejects_without_gcp_credentials(self, client, admin_token, session):
        """Background deploy fails if GCP credentials are not configured."""
        await _set_config(session, "gcp_credentials_configured", "false")
        await _set_config(session, "terraform_initialized", "true")
        await _set_config(session, "compute_deployed", "false")
        await session.commit()

        response = await client.post(
            "/api/v1/infrastructure/stack/deploy-background",
            json={"stack_type": "kubernetes"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_without_terraform_init(self, client, admin_token, session):
        """Background deploy fails if terraform is not initialized."""
        await _set_config(session, "gcp_credentials_configured", "true")
        await _set_config(session, "terraform_initialized", "false")
        await _set_config(session, "compute_deployed", "false")
        await session.commit()

        response = await client.post(
            "/api/v1/infrastructure/stack/deploy-background",
            json={"stack_type": "kubernetes"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 400
