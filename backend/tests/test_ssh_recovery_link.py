"""Plan 03B (D03-9): Restore SSH access is a read-only link to this exact server's cloud console.

Gated by effective infrastructure:configure (custom roles included, no deploy permission needed);
callers without it receive no host metadata. The link is built from the recorded immutable binding
and a fixed provider origin; a replaced host is reconciled, never trusted by name alone.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.user import User
from app.services import role_service
from app.services.auth_service import AuthService
from app.services.ssh_recovery import HostIdentity, restore_access

GCP_PUBLIC = HostIdentity(
    provider="gcp",
    account="lab-project",
    region="us-central1",
    zone="us-central1-b",
    instance_id="4815162342",
    instance_name="bioaf",
    public=True,
    os_username="",
)
GCP_PRIVATE = HostIdentity(**{**GCP_PUBLIC.__dict__, "public": False})
AWS_PUBLIC = HostIdentity(
    provider="aws",
    account="123456789012",
    region="us-west-1",
    zone="us-west-1b",
    instance_id="i-0abc123def4567890",
    instance_name="bioaf",
    public=True,
    os_username="ubuntu",
    partition="aws",
)
AWS_PRIVATE = HostIdentity(**{**AWS_PUBLIC.__dict__, "public": False, "eic_endpoint_id": "eice-0123456789abcdef0"})


def test_private_gcp_opens_the_instance_page_and_says_choose_ssh():
    link = restore_access(GCP_PRIVATE)
    assert link["url"] == (
        "https://console.cloud.google.com/compute/instancesDetail/zones/us-central1-b/instances/bioaf?project=lab-project"
    )
    assert link["instruction"] == "Sign in to the Google Cloud account used for this server, then choose SSH."
    assert link["command"] is None


def test_public_gcp_uses_cloud_shell_with_the_exact_iap_command():
    link = restore_access(GCP_PUBLIC)
    assert link["instruction"] == (
        "Open Cloud Shell on this server's page, paste the recovery command below, and press Enter."
    )
    assert link["command"] == "gcloud compute ssh bioaf --project=lab-project --zone=us-central1-b --tunnel-through-iap"


def test_public_aws_uses_ec2_instance_connect_with_the_username():
    link = restore_access(AWS_PUBLIC)
    assert link["url"] == (
        "https://us-west-1.console.aws.amazon.com/ec2/home?region=us-west-1#ConnectToInstance:instanceId=i-0abc123def4567890"
    )
    assert link["instruction"] == (
        "Sign in to the AWS account used for this server, choose Connect, then EC2 Instance Connect and Connect."
    )
    assert link["os_username"] == "ubuntu"
    assert link["address_choice"] == "Public IP"


def test_private_aws_selects_private_ip_and_the_prepared_endpoint():
    link = restore_access(AWS_PRIVATE)
    assert link["address_choice"] == "Private IP"
    assert link["endpoint_id"] == "eice-0123456789abcdef0"


@pytest.mark.parametrize(
    "partition, origin",
    [
        ("aws-cn", "https://cn-north-1.console.amazonaws.cn"),
        ("aws-us-gov", "https://us-gov-west-1.console.amazonaws-us-gov.com"),
    ],
)
def test_aws_links_are_partition_aware(partition, origin):
    region = "cn-north-1" if partition == "aws-cn" else "us-gov-west-1"
    identity = HostIdentity(**{**AWS_PUBLIC.__dict__, "partition": partition, "region": region})
    assert restore_access(identity)["url"].startswith(origin + "/ec2/home?region=")


@pytest.mark.parametrize(
    "field, value",
    [
        ("instance_name", "bioaf;rm -rf /"),
        ("zone", "us-central1-b --flag"),
        ("account", "proj?x=1"),
        ("instance_id", "i-$(id)"),
    ],
)
def test_unsafe_identity_values_never_reach_a_link_or_command(field, value):
    from app.services.ssh_recovery import RecoveryError

    identity = HostIdentity(**{**(AWS_PUBLIC if field == "instance_id" else GCP_PUBLIC).__dict__, field: value})
    with pytest.raises(RecoveryError):
        restore_access(identity)


# -- The API ----------------------------------------------------------------------------------


async def _custom(session, org_id, email, grants):
    role = await role_service.create_role(session, org_id, f"custom-{email}", permissions=grants)
    user = User(
        email=email,
        password_hash=AuthService.hash_password("customrolepass1"),
        role_id=role.id,
        organization_id=org_id,
        status="active",
    )
    session.add(user)
    await session.commit()
    role_service.invalidate_cache()
    return {
        "Authorization": f"Bearer {AuthService.create_token(user.id, user.email, role.id, org_id, role_name=role.name)}"
    }


@pytest.mark.asyncio
async def test_a_custom_role_with_infrastructure_configure_gets_the_link_without_deploy(client, session, admin_user):
    headers = await _custom(session, admin_user.organization_id, "ops@lab.test", [("infrastructure", "configure")])
    with patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=GCP_PRIVATE)):
        response = await client.get("/api/v1/settings/ssh-access/restore", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("https://console.cloud.google.com/")
    assert body["server"] == {
        "provider": "gcp",
        "account": "lab-project",
        "zone": "us-central1-b",
        "instance": "bioaf",
        "instance_id": "4815162342",
    }


@pytest.mark.asyncio
async def test_callers_without_infrastructure_configure_receive_no_host_metadata(
    client, session, admin_user, comp_bio_token
):
    headers = await _custom(
        session,
        admin_user.organization_id,
        "deploy@lab.test",
        [("infrastructure", "deploy"), ("work_nodes", "configure")],
    )
    with patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=GCP_PRIVATE)):
        for h in (headers, {"Authorization": f"Bearer {comp_bio_token}"}):
            response = await client.get("/api/v1/settings/ssh-access/restore", headers=h)
            assert response.status_code == 403
            assert "lab-project" not in response.text


@pytest.mark.asyncio
async def test_an_unknown_host_identity_says_so_instead_of_linking_somewhere(client, admin_token):
    from app.services.ssh_recovery import RecoveryError

    with patch(
        "app.services.ssh_recovery.current_host_identity", AsyncMock(side_effect=RecoveryError("not on a cloud VM"))
    ):
        response = await client.get(
            "/api/v1/settings/ssh-access/restore", headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert response.status_code == 200
    assert response.json()["url"] is None
    assert "could not identify" in response.json()["message"]


@pytest.mark.asyncio
async def test_a_replaced_host_is_reconciled_by_immutable_id(session):
    from app.services.ssh_recovery import reconcile_host_identity

    first = await reconcile_host_identity(session, GCP_PRIVATE)
    assert first.replaced is False
    replacement = HostIdentity(**{**GCP_PRIVATE.__dict__, "instance_id": "999"})
    second = await reconcile_host_identity(session, replacement)
    assert second.replaced is True
    assert second.identity.instance_id == "999"


@pytest.mark.asyncio
async def test_a_private_aws_server_links_to_its_prepared_endpoint(session):
    from unittest.mock import MagicMock

    from app.services.ssh_recovery import current_host_identity

    discovered = HostIdentity(**{**AWS_PRIVATE.__dict__, "eic_endpoint_id": ""})
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"VpcId": "vpc-app"}]}]}
    ec2.describe_instance_connect_endpoints.return_value = {
        "InstanceConnectEndpoints": [
            {"InstanceConnectEndpointId": "eice-0123456789abcdef0", "State": "create-complete"}
        ]
    }
    with (
        patch("app.services.ssh_recovery._metadata_identity", AsyncMock(return_value=discovered)),
        patch("app.config.settings.compute_mode", "kubernetes"),
        patch("boto3.client", return_value=ec2),
    ):
        identity = await current_host_identity(session)
    assert restore_access(identity)["endpoint_id"] == "eice-0123456789abcdef0"
