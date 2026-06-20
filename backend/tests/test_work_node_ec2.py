"""EC2 work-node provider tests (cleanup item 8b-launch).

The AWS analog of the GCE work-node adapter: launch an EC2 instance from a built
AMI (resolved by name), into the compute VPC's subnet + work-node SG with the
work-node instance profile, with a user-data startup script that stages inputs /
syncs outputs via ``aws s3``. boto3 is mocked at the provider ``_client`` seam, so
these are DB-free and run locally per the repo conventions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.work_nodes.ec2 import Ec2VmInstance, Ec2WorkNodeProvider, _build_ec2_startup_script
from app.exceptions import ValidationError


def _aws_provider() -> Ec2VmInstance:
    """An EC2 provider in real (non-local) mode with networking config preloaded."""
    p = Ec2VmInstance()
    p._mode = "aws"
    p._aws_config = {
        "aws_region": "us-west-1",
        "working_bucket_name": "bioaf-working-5f6286",
        "aws_work_node_subnet_id": "subnet-abc",
        "aws_work_node_security_group_id": "sg-xyz",
        "aws_work_node_instance_profile": "bioaf-work-node-887a06",
    }
    return p


def _vm_spec(**over) -> dict:
    spec = {
        "session_id": 7,
        "user_id": 3,
        "machine_type": "n2-standard-4",
        "ec2_instance_type": "m5.xlarge",
        "image_uri": "bioaf-worknode-bench-env-v1-1",
        "input_files": [{"file_id": 1, "gcs_uri": "s3://bioaf-raw-5f6286/a.fastq.gz", "relative_path": "a.fastq.gz"}],
        "heartbeat_token": "tok",
        "session_credentials": {"username": "scientist", "password_hash": "$2b$xx"},
        "ssh_public_key": "",
        "ssh_private_key": "",
        "github_repos": [],
        "conda_env_name": "bioaf-work",
        "environment_label": "Bench Env v1",
        "working_bucket": "bioaf-working-5f6286",
        "boot_disk_gb": 100,
    }
    spec.update(over)
    return spec


@pytest.mark.asyncio
async def test_ec2_launch_runs_instance_with_resolved_ami_and_networking():
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_images.return_value = {
        "Images": [
            {"ImageId": "ami-old", "CreationDate": "2026-06-01T00:00:00Z"},
            {"ImageId": "ami-new", "CreationDate": "2026-06-18T00:00:00Z"},
        ]
    }
    ec2.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-0abc", "Placement": {"AvailabilityZone": "us-west-1a"}}]
    }

    with (
        patch.object(p, "_client", return_value=ec2),
        patch.object(p, "_poll_vm_ready", new=AsyncMock()),
    ):
        info = await p.provision(_vm_spec())

    assert info.instance_name == "i-0abc"
    assert info.zone == "us-west-1a"
    assert info.status.value == "starting"

    kwargs = ec2.run_instances.call_args.kwargs
    assert kwargs["ImageId"] == "ami-new"  # most recent AMI for the name
    assert kwargs["InstanceType"] == "m5.xlarge"
    ni = kwargs["NetworkInterfaces"][0]
    assert ni["SubnetId"] == "subnet-abc"
    assert ni["Groups"] == ["sg-xyz"]
    assert ni["AssociatePublicIpAddress"] is True
    assert kwargs["IamInstanceProfile"] == {"Name": "bioaf-work-node-887a06"}
    assert kwargs["BlockDeviceMappings"][0]["Ebs"]["VolumeType"] == "gp3"
    tags = {t["Key"]: t["Value"] for t in kwargs["TagSpecifications"][0]["Tags"]}
    assert tags["bioaf-managed"] == "true"
    assert tags["bioaf-session"] == "7"
    # User-data is the startup script (staged via aws s3, not gsutil).
    assert "aws s3 cp" in kwargs["UserData"]
    assert "gsutil" not in kwargs["UserData"]


@pytest.mark.asyncio
async def test_ec2_launch_requires_networking():
    p = _aws_provider()
    p._aws_config["aws_work_node_subnet_id"] = ""
    with patch.object(p, "_client", return_value=MagicMock()):
        with pytest.raises(ValidationError, match="networking is not deployed"):
            await p.provision(_vm_spec())


@pytest.mark.asyncio
async def test_ec2_launch_requires_image_uri():
    p = _aws_provider()
    with patch.object(p, "_client", return_value=MagicMock()):
        with pytest.raises(ValidationError, match="image_uri"):
            await p.provision(_vm_spec(image_uri=""))


def test_resolve_ami_id_passthrough_and_lookup():
    p = _aws_provider()
    ec2 = MagicMock()
    # A raw ami-id passes through without a lookup.
    assert p._resolve_ami_id(ec2, "ami-12345") == "ami-12345"
    ec2.describe_images.assert_not_called()

    # A name resolves to the most-recent matching AMI.
    ec2.describe_images.return_value = {
        "Images": [
            {"ImageId": "ami-a", "CreationDate": "2026-01-01T00:00:00Z"},
            {"ImageId": "ami-b", "CreationDate": "2026-06-18T00:00:00Z"},
        ]
    }
    assert p._resolve_ami_id(ec2, "bioaf-worknode-x-v1-1") == "ami-b"


def test_resolve_ami_id_not_found_raises():
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_images.return_value = {"Images": []}
    with pytest.raises(ValidationError, match="image not found"):
        p._resolve_ami_id(ec2, "bioaf-worknode-missing-v1-1")


def test_ec2_startup_script_uses_aws_s3_not_gsutil():
    script = _build_ec2_startup_script(_vm_spec())
    assert "aws s3 cp" in script  # input staging
    assert "aws s3 sync /outputs s3://bioaf-working-5f6286/sessions/7/outputs/" in script  # shutdown sync
    assert "gsutil" not in script
    assert "gs://" not in script
    assert "conda activate bioaf-work" in script
    # Heartbeat + PAM user are cloud-agnostic and preserved.
    assert "useradd -m -d /home/scientist" in script


def test_ec2_startup_script_enables_password_auth():
    """Work nodes are reached via PAM/password SSH (session credentials). AWS Ubuntu
    AMIs default to PasswordAuthentication no (in the cloudimg drop-in, which wins),
    so the startup script must flip it and restart sshd or login is publickey-only."""
    script = _build_ec2_startup_script(_vm_spec())
    # Neutralizes the cloudimg drop-in that wins on first match...
    assert "/etc/ssh/sshd_config.d/*.conf" in script
    assert "PasswordAuthentication yes" in script
    # ...and reloads sshd so the change takes effect at boot.
    assert "restart ssh" in script


@pytest.mark.asyncio
async def test_ec2_terminate_terminates_instance():
    p = _aws_provider()
    ec2 = MagicMock()
    # No public IP -> SSH output sync is skipped; instance is still terminated.
    ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{"InstanceId": "i-0abc"}]}]}
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [{"Contents": []}]

    def client(service):
        return s3 if service == "s3" else ec2

    with patch.object(p, "_client", side_effect=client):
        result = await p.delete("i-0abc", "us-west-1a", session_id=7, working_bucket="bioaf-working-5f6286")

    ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-0abc"])
    assert result.output_prefix == "s3://bioaf-working-5f6286/sessions/7/outputs/"


@pytest.mark.asyncio
async def test_ec2_status_maps_running_state():
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "State": {"Name": "running"},
                        "PublicIpAddress": "1.2.3.4",
                        "Placement": {"AvailabilityZone": "us-west-1b"},
                    }
                ]
            }
        ]
    }
    with patch.object(p, "_client", return_value=ec2):
        status = await p.inspect("i-0abc", "us-west-1a")
    assert status.external_ip == "1.2.3.4"
    assert status.zone == "us-west-1b"
    assert status.status.value == "running"


def test_registry_creates_ec2_work_node_provider():
    from app.adapters.registry import _create_work_node_adapter
    from app.adapters.work_nodes.gce import GCEWorkNodeProvider

    ec2 = _create_work_node_adapter("ec2")
    assert isinstance(ec2, Ec2WorkNodeProvider)
    assert ec2.capabilities().work_nodes is True

    # GCP unchanged: the default backend still builds the GCE provider.
    gce = _create_work_node_adapter("gce")
    assert isinstance(gce, GCEWorkNodeProvider)


def test_every_machine_type_has_an_ec2_instance_type():
    from app.services.machine_types import MACHINE_TYPES

    for mt in MACHINE_TYPES:
        assert mt.get("ec2_instance_type"), f"{mt['name']} is missing ec2_instance_type"
