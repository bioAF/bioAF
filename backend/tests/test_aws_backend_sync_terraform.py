"""Plan 03B: the AWS compute module peers the compute VPC with the application VPC for backend sync.

Text checks of the module (the repository's Terraform test style) plus the tfvars, outputs and the
application-network discovery that feed it. A Terraform plan or apply is not run here.
"""

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MODULE = Path(__file__).resolve().parents[1] / "terraform" / "aws" / "modules" / "compute"


def _main():
    return (MODULE / "main.tf").read_text()


def test_peering_routes_and_sync_group_exist_only_when_the_app_vpc_is_known():
    main = _main()
    variables = (MODULE / "variables.tf").read_text()
    assert re.search(r'variable "app_vpc_id" \{[^}]*default\s*=\s*""', variables, re.S)
    assert re.search(r'variable "app_security_group_id" \{[^}]*default\s*=\s*""', variables, re.S)
    peering = re.search(r'resource "aws_vpc_peering_connection" "app" \{(.*?)\n\}', main, re.S).group(1)
    assert "count       = local.backend_sync_private ? 1 : 0" in peering
    assert "peer_vpc_id = var.app_vpc_id" in peering
    assert "auto_accept = true" in peering
    assert (
        'local.backend_sync_private = var.app_vpc_id != "" && var.app_security_group_id != ""'
        in main.replace("  ", " ")
        or 'backend_sync_private = var.app_vpc_id != "" && var.app_security_group_id != ""' in main
    )


def test_routes_are_added_both_ways_without_replacing_existing_routes():
    main = _main()
    assert 'resource "aws_route" "compute_to_app"' in main
    assert "destination_cidr_block    = data.aws_vpc.app[0].cidr_block" in main
    assert 'resource "aws_route" "app_to_compute"' in main
    assert "destination_cidr_block    = aws_vpc.this.cidr_block" in main
    # Only explicit peer routes: the default internet routes are left alone.
    assert main.count('cidr_block = "0.0.0.0/0"') == 1


def test_the_sync_group_allows_ssh_only_from_the_application_group():
    main = _main()
    block = re.search(r'resource "aws_security_group" "backend_sync" \{(.*?)\n\}', main, re.S).group(1)
    assert "from_port       = 22" in block and "to_port         = 22" in block
    assert "security_groups = [var.app_security_group_id]" in block
    assert "cidr_blocks" not in block.split("egress")[0]
    outputs = (MODULE / "outputs.tf").read_text()
    assert 'output "backend_sync_security_group_id"' in outputs


def test_overlapping_vpcs_are_refused_before_anything_is_created():
    main = _main()
    assert "precondition" in main and "data.aws_vpc.app[0].cidr_block != aws_vpc.this.cidr_block" in main


def test_tfvars_pass_the_recorded_application_network(tmp_path):
    from app.services.terraform_cloud import get_terraform_cloud

    cloud = get_terraform_cloud("aws")
    assert {"aws_app_vpc_id", "aws_app_security_group_id"} <= set(cloud.config_keys())
    tfvars = cloud.write_tfvars(
        tmp_path,
        "compute",
        {"aws_region": "us-west-1", "aws_app_vpc_id": "vpc-app", "aws_app_security_group_id": "sg-app"},
    )
    assert tfvars["app_vpc_id"] == "vpc-app" and tfvars["app_security_group_id"] == "sg-app"
    assert json.loads((tmp_path / "terraform.tfvars.json").read_text())["app_vpc_id"] == "vpc-app"
    bare = cloud.write_tfvars(tmp_path, "compute", {"aws_region": "us-west-1"})
    assert "app_vpc_id" not in bare


def test_the_sync_group_output_is_recorded_for_launches():
    from app.services.deployment_outputs import COMPUTE_AWS_OUTPUTS

    assert COMPUTE_AWS_OUTPUTS["backend_sync_security_group_id"] == "aws_backend_sync_security_group_id"


@pytest.mark.asyncio
async def test_the_application_network_is_discovered_from_the_running_instance(session):
    from app.platform.platform_config_service import PlatformConfigService
    from app.services.ssh_recovery import HostIdentity, record_aws_app_network

    identity = HostIdentity(
        provider="aws",
        account="123456789012",
        region="us-west-1",
        zone="us-west-1b",
        instance_id="i-0abc123def4567890",
        instance_name="bioaf",
        public=True,
    )
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "VpcId": "vpc-app",
                        "SecurityGroups": [
                            {"GroupId": "sg-recovery", "GroupName": "bioaf-ssh-recovery"},
                            {"GroupId": "sg-app", "GroupName": "bioaf-app"},
                        ],
                    }
                ]
            }
        ]
    }
    with (
        patch("app.services.ssh_recovery.current_host_identity", AsyncMock(return_value=identity)),
        patch("boto3.client", return_value=ec2),
    ):
        await record_aws_app_network(session)
    assert await PlatformConfigService.get(session, "aws_app_vpc_id") == "vpc-app"
    assert await PlatformConfigService.get(session, "aws_app_security_group_id") == "sg-app"
