"""GET /api/v1/settings/gcp surfaces gcp_bootstrap_sa_email so the wizard
can show users the auto-detected impersonation target.
"""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_response_includes_bootstrap_email_when_set(session, client, admin_token):
    await session.execute(
        text(
            "INSERT INTO platform_config (key, value) VALUES (:k, :v) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ).bindparams(k="gcp_bootstrap_sa_email", v="bioaf-bootstrap@my-project.iam.gserviceaccount.com")
    )
    await session.commit()

    resp = await client.get(
        "/api/v1/settings/gcp",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["gcp_bootstrap_sa_email"] == "bioaf-bootstrap@my-project.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_response_omits_bootstrap_email_when_unset(session, client, admin_token):
    resp = await client.get(
        "/api/v1/settings/gcp",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("gcp_bootstrap_sa_email") is None
