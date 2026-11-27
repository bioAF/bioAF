"""Administrator API for the EKS Kubernetes API endpoint (plan 04C).

GET reports the recorded mode and whether preserved access is still pending hardening. POST
records a reviewed cutover that the next compute plan and apply carry out; DELETE drops a pending
cutover while the live endpoint is unchanged. AWS lookups are faked.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.platform.platform_config_service import PlatformConfigService

URL = "/api/v1/infrastructure/eks-endpoint"
ARN = "arn:aws:eks:us-west-1:123456789012:cluster/bioaf-acme-c0ffee"
SNAPSHOT = {
    "cluster_arn": ARN,
    "cluster_name": "bioaf-acme-c0ffee",
    "created_at": "2026-06-18T10:00:00+00:00",
    "endpoint_public_access": True,
    "endpoint_private_access": True,
    "public_access_cidrs": ["0.0.0.0/0"],
}
LIVE = {
    "arn": ARN,
    "name": "bioaf-acme-c0ffee",
    "createdAt": datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc),
    "resourcesVpcConfig": {
        "endpointPublicAccess": True,
        "endpointPrivateAccess": True,
        "publicAccessCidrs": ["0.0.0.0/0"],
    },
}


async def _preserved(session):
    for key, value in {
        "cloud_provider": "aws",
        "aws_region": "us-west-1",
        "compute_deployed": "true",
        "aws_app_vpc_id": "vpc-app",
        "aws_app_security_group_id": "sg-app",
        "aws_eks_endpoint_mode": "preserve_existing",
        "aws_eks_endpoint_snapshot": json.dumps(SNAPSHOT),
    }.items():
        await PlatformConfigService.set(session, key, value)
    await session.commit()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_status_shows_preserved_unrestricted_access_as_pending_hardening(client, admin_token, session):
    await _preserved(session)
    response = await client.get(URL, headers=_auth(admin_token))
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "preserve_existing"
    assert body["pending_hardening"] is True
    assert body["public_access_cidrs"] == ["0.0.0.0/0"]
    assert body["cutover"] is None


@pytest.mark.asyncio
async def test_status_is_not_available_on_gcp(client, admin_token, session):
    await PlatformConfigService.set(session, "cloud_provider", "gcp")
    await session.commit()
    response = await client.get(URL, headers=_auth(admin_token))
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_a_cutover_is_recorded_and_audited(client, admin_token, session):
    await _preserved(session)
    with patch("app.adapters.eks_control_plane.aws.describe_cluster", return_value=LIVE):
        response = await client.post(
            f"{URL}/cutover",
            headers=_auth(admin_token),
            json={"mode": "private_only", "recovery_paths_verified": True},
        )
    assert response.status_code == 200, response.text
    assert response.json()["cutover"]["mode"] == "private_only"
    assert response.json()["mode"] == "preserve_existing"
    actions = (
        (await session.execute(text("SELECT action FROM audit_log WHERE entity_type = 'eks_endpoint'"))).scalars().all()
    )
    assert "cutover_requested" in actions


@pytest.mark.asyncio
async def test_an_unrestricted_public_cutover_is_refused(client, admin_token, session):
    await _preserved(session)
    with patch("app.adapters.eks_control_plane.aws.describe_cluster", return_value=LIVE):
        response = await client.post(
            f"{URL}/cutover",
            headers=_auth(admin_token),
            json={"mode": "public_restricted", "public_access_cidrs": ["0.0.0.0/0"], "recovery_paths_verified": True},
        )
    assert response.status_code in (400, 422)
    assert not await PlatformConfigService.get(session, "aws_eks_endpoint_cutover")


@pytest.mark.asyncio
async def test_a_pending_cutover_can_be_cancelled(client, admin_token, session):
    await _preserved(session)
    with patch("app.adapters.eks_control_plane.aws.describe_cluster", return_value=LIVE):
        await client.post(
            f"{URL}/cutover", headers=_auth(admin_token), json={"mode": "private_only", "recovery_paths_verified": True}
        )
        response = await client.delete(f"{URL}/cutover", headers=_auth(admin_token))
    assert response.status_code == 200
    assert response.json()["cutover"] is None


@pytest.mark.asyncio
async def test_scientists_cannot_schedule_a_cutover(client, bench_token, session):
    await _preserved(session)
    response = await client.post(
        f"{URL}/cutover", headers=_auth(bench_token), json={"mode": "private_only", "recovery_paths_verified": True}
    )
    assert response.status_code == 403
