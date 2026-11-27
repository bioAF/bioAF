"""EC2 work-node provider tests (cleanup item 8b-launch).

The AWS analog of the GCE work-node adapter: launch an EC2 instance from a built
AMI (resolved by name), into the compute VPC's subnet + work-node SG with the
work-node instance profile, with a user-data startup script that stages inputs /
syncs outputs via ``aws s3``. boto3 is mocked at the provider ``_client`` seam, so
these are DB-free and run locally per the repo conventions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.settings_contract import ComputeMode
from tests.support.mode_adapters import build_mode_adapter
from app.adapters.work_nodes.ec2 import Ec2VmInstance, Ec2WorkNodeProvider, _build_ec2_startup_script
from app.exceptions import ValidationError


def _aws_provider() -> Ec2VmInstance:
    """An EC2 provider in real (non-local) mode with networking config preloaded."""
    p = build_mode_adapter(Ec2VmInstance, mode=ComputeMode.KUBERNETES)
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
    assert p._aws_config is not None
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
    """Work nodes are reached via PAM/password SSH (session credentials) when no Work Node range is set.

    Owner-approved change (2026-11-24, plan 03B): instead of a global PasswordAuthentication override
    (which an AWS cloudimg default beats and which cannot express a range), the scientist's password
    login comes from an account-scoped Match rule that sshd validates before it is reloaded."""
    script = _build_ec2_startup_script(_vm_spec())
    assert "Match User scientist" in script
    assert "    PasswordAuthentication yes" in script
    assert "/usr/sbin/sshd -t" in script
    assert "systemctl reload ssh" in script
    # No global override is written, and an older one is removed.
    assert "printf 'PasswordAuthentication yes\\n' > /etc/ssh/sshd_config.d/99-bioaf-password-auth.conf" not in script
    assert "rm -f /etc/ssh/sshd_config.d/99-bioaf-password-auth.conf" in script


def _instance(state="running", ip="1.2.3.4", session="7", attempt="attempt-1"):
    tags = [{"Key": "bioaf-session", "Value": session}, {"Key": "bioaf-launch", "Value": attempt}]
    inst = {"InstanceId": "i-0abc", "State": {"Name": state}, "Tags": tags}
    if ip:
        inst["PublicIpAddress"] = ip
    return {"Reservations": [{"Instances": [inst]}]}


def _clients(ec2, s3=None):
    s3 = s3 or MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "sessions/7/outputs/result.csv", "Size": 12}]}
    ]

    def client(service):
        return s3 if service == "s3" else ec2

    return client


_CREDS = dict(
    session_id=7,
    working_bucket="bioaf-working-5f6286",
    sync_private_key="sync-private",
    host_public_key="ssh-ed25519 AAAAhost",
    launch_attempt_id="attempt-1",
)


async def _delete(p, ec2, sync=AsyncMock(return_value=0), **over):
    kwargs = {**_CREDS, **over}
    p._delete_poll_interval = 0
    with (
        patch.object(p, "_client", side_effect=_clients(ec2)),
        patch("app.adapters.work_nodes.ec2.run_sync_command", sync),
    ):
        return await p.delete("i-0abc", "us-west-1a", **kwargs)


@pytest.mark.asyncio
async def test_ec2_terminate_keeps_a_running_instance_without_an_address():
    """Owner-approved change (2026-11-24, plan 03A): an unsaved node is kept, never terminated."""
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_instances.return_value = _instance(ip=None)
    sync = AsyncMock(return_value=0)
    result = await _delete(p, ec2, sync)

    ec2.terminate_instances.assert_not_called()
    sync.assert_not_called()
    assert result.stop_failure == "save_failed"
    assert result.status.value == "running"
    assert result.output_files == []


@pytest.mark.asyncio
async def test_ec2_terminate_saves_with_the_recorded_identity_then_confirms_termination():
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = [_instance(), _instance(state="terminated")]
    sync = AsyncMock(return_value=0)
    result = await _delete(p, ec2, sync)

    call = sync.call_args
    assert call.args[0] == "1.2.3.4"
    assert call.kwargs["sync_private_key"] == "sync-private"
    assert call.kwargs["host_public_key"] == "ssh-ed25519 AAAAhost"
    # A failed output sync fails the save (owner decision 2026-11-25: Stop saves /outputs only).
    assert "|| exit 1" in call.kwargs["command"]
    ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-0abc"])
    assert result.stop_failure is None
    assert result.status.value == "stopped"
    assert [o.filename for o in result.output_files] == ["result.csv"]
    assert result.output_prefix == "s3://bioaf-working-5f6286/sessions/7/outputs/"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sync",
    [
        AsyncMock(return_value=1),
        AsyncMock(side_effect=__import__("app.adapters.work_nodes.node_ssh", fromlist=["x"]).NodeTrustError("x")),
        AsyncMock(side_effect=__import__("app.adapters.work_nodes.node_ssh", fromlist=["x"]).NodeSyncError("x")),
    ],
    ids=["nonzero", "untrusted-server", "unreachable"],
)
async def test_ec2_terminate_keeps_the_instance_when_saving_fails(sync):
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_instances.return_value = _instance()
    result = await _delete(p, ec2, sync)
    ec2.terminate_instances.assert_not_called()
    assert result.stop_failure == "save_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["sync_private_key", "host_public_key", "working_bucket"])
async def test_ec2_terminate_keeps_the_instance_without_credentials_or_bucket(missing):
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_instances.return_value = _instance()
    sync = AsyncMock(return_value=0)
    result = await _delete(p, ec2, sync, **{missing: ""})
    ec2.terminate_instances.assert_not_called()
    sync.assert_not_called()
    assert result.stop_failure == "save_failed"


@pytest.mark.asyncio
async def test_ec2_terminate_never_touches_an_instance_bound_to_another_session():
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_instances.return_value = _instance(session="8")
    sync = AsyncMock(return_value=0)
    result = await _delete(p, ec2, sync)
    ec2.terminate_instances.assert_not_called()
    sync.assert_not_called()
    assert result.stop_failure == "state_unknown"


@pytest.mark.asyncio
async def test_ec2_terminate_reports_unknown_when_the_instance_cannot_be_described():
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_instances.side_effect = RuntimeError("throttled")
    result = await _delete(p, ec2)
    ec2.terminate_instances.assert_not_called()
    assert result.stop_failure == "state_unknown"


@pytest.mark.asyncio
async def test_ec2_terminate_reports_unconfirmed_deletion_after_a_save():
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_instances.return_value = _instance()  # never reaches terminated
    result = await _delete(p, ec2)
    ec2.terminate_instances.assert_called_once()
    assert result.stop_failure == "delete_unconfirmed"


@pytest.mark.asyncio
async def test_ec2_terminate_of_an_already_terminated_instance_is_stopped():
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_instances.return_value = _instance(state="terminated")
    sync = AsyncMock(return_value=0)
    result = await _delete(p, ec2, sync)
    sync.assert_not_called()
    ec2.terminate_instances.assert_not_called()
    assert result.stop_failure is None
    assert result.status.value == "stopped"


@pytest.mark.asyncio
async def test_ec2_launch_installs_the_launch_host_key_and_tags_the_attempt():
    from app.adapters.work_nodes.node_ssh import generate_node_identity

    identity = generate_node_identity()
    p = _aws_provider()
    ec2 = MagicMock()
    ec2.describe_images.return_value = {"Images": [{"ImageId": "ami-1", "CreationDate": "2026"}]}
    ec2.run_instances.return_value = {
        "OwnerId": "123456789012",
        "Instances": [{"InstanceId": "i-0new", "Placement": {"AvailabilityZone": "us-west-1b"}}],
    }
    spec = _vm_spec(
        sync_public_key=identity.sync_public_key,
        host_private_key=identity.host_private_key,
        host_public_key=identity.host_public_key,
        launch_attempt_id="attempt-9",
    )
    with (
        patch.object(p, "_client", return_value=ec2),
        patch.object(p, "_poll_vm_ready", new=MagicMock()),
        patch("app.adapters.work_nodes.ec2.runtime.spawn"),
    ):
        info = await p.provision(spec)

    kwargs = ec2.run_instances.call_args.kwargs
    assert "/etc/ssh/bioaf_host_ed25519_key" in kwargs["UserData"]
    assert identity.sync_public_key in kwargs["UserData"]
    tags = {t["Key"]: t["Value"] for t in kwargs["TagSpecifications"][0]["Tags"]}
    assert tags["bioaf-launch"] == "attempt-9"
    assert info.provider_details["provider_instance_id"] == "i-0new"
    assert info.provider_details["aws_account_id"] == "123456789012"


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


# -- Recorded launch requests and their settled outcome (review 3, item 4) ----------------------


@pytest.mark.asyncio
async def test_ec2_launch_records_the_request_before_sending_it_with_the_attempt_as_client_token():
    p = _aws_provider()
    events: list[tuple] = []

    async def record(entry):
        events.append(("record", entry["state"], entry["client_token"]))

    ec2 = MagicMock()
    ec2.describe_images.return_value = {"Images": [{"ImageId": "ami-1", "CreationDate": "2026"}]}

    def run(**kwargs):
        events.append(("run", kwargs.get("ClientToken")))
        return {
            "OwnerId": "1",
            "Instances": [{"InstanceId": "i-0new", "Placement": {"AvailabilityZone": "us-west-1b"}}],
        }

    ec2.run_instances.side_effect = run
    spec = _vm_spec(launch_attempt_id="attempt9", record_launch_request=record)
    with (
        patch.object(p, "_client", return_value=ec2),
        patch.object(p, "_poll_vm_ready", new=MagicMock()),
        patch("app.adapters.work_nodes.ec2.runtime.spawn"),
    ):
        await p.provision(spec)
    assert events == [("record", "requested", "attempt9"), ("run", "attempt9"), ("record", "created", "attempt9")]


@pytest.mark.asyncio
async def test_ec2_a_refused_request_is_recorded_as_failed():
    from botocore.exceptions import ClientError

    p = _aws_provider()
    states: list[str] = []

    async def record(entry):
        states.append(entry["state"])

    ec2 = MagicMock()
    ec2.describe_images.return_value = {"Images": [{"ImageId": "ami-1", "CreationDate": "2026"}]}
    ec2.run_instances.side_effect = ClientError({"Error": {"Code": "InsufficientInstanceCapacity"}}, "RunInstances")
    with (
        patch.object(p, "_client", return_value=ec2),
        pytest.raises(Exception),
    ):
        await p.provision(_vm_spec(launch_attempt_id="attempt9", record_launch_request=record))
    assert states == ["requested", "failed"]


async def _ec2_settle(requests, reservations=(), error=None):
    p = _aws_provider()
    ec2 = MagicMock()
    if error:
        ec2.describe_instances.side_effect = error
    else:
        ec2.describe_instances.return_value = {"Reservations": [{"Instances": list(reservations)}]}
    with patch.object(p, "_client", return_value=ec2):
        return await p.settle_launch(requests, session_id=7), ec2


EC2_REQ = {"client_token": "attempt9", "region": "us-west-1", "state": "requested"}
EC2_INSTANCE = {"InstanceId": "i-0new", "State": {"Name": "pending"}, "Placement": {"AvailabilityZone": "us-west-1b"}}


@pytest.mark.asyncio
async def test_ec2_settlement_follows_the_recorded_answer():
    assert (await _ec2_settle([]))[0].state == "none"
    assert (await _ec2_settle([{**EC2_REQ, "state": "failed"}]))[0].state == "none"
    outcome, ec2 = await _ec2_settle([EC2_REQ], reservations=[EC2_INSTANCE])
    assert outcome.state == "created" and outcome.instance.name == "i-0new"
    assert {"Name": "client-token", "Values": ["attempt9"]} in ec2.describe_instances.call_args.kwargs["Filters"]


@pytest.mark.asyncio
async def test_ec2_an_unanswered_request_with_no_listed_instance_stays_unknown():
    """DescribeInstances is eventually consistent, so absence after an unanswered request is not proof."""
    assert (await _ec2_settle([EC2_REQ]))[0].state == "unknown"
    assert (await _ec2_settle([EC2_REQ], error=RuntimeError("denied")))[0].state == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,status,recorded",
    [
        ("InternalError", 500, ["requested"]),
        ("Unavailable", 503, ["requested"]),
        ("InvalidParameterValue", 400, ["requested", "failed"]),
        ("InsufficientInstanceCapacity", 500, ["requested", "failed"]),
    ],
)
async def test_ec2_only_a_definite_refusal_is_recorded_as_failed(code, status, recorded):
    """Review 4 item 1: a 5xx may have been processed, so it stays "requested" for settlement; a 4xx
    refusal or a documented capacity error launched nothing."""
    from botocore.exceptions import ClientError

    p = _aws_provider()
    states: list[str] = []

    async def record(entry):
        states.append(entry["state"])

    ec2 = MagicMock()
    ec2.describe_images.return_value = {"Images": [{"ImageId": "ami-1", "CreationDate": "2026"}]}
    ec2.run_instances.side_effect = ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, "RunInstances"
    )
    with patch.object(p, "_client", return_value=ec2), pytest.raises(Exception):
        await p.provision(_vm_spec(launch_attempt_id="attempt9", record_launch_request=record))
    assert states == recorded


@pytest.mark.asyncio
async def test_ec2_launch_tags_the_instance_volume_and_interface_as_this_installations():
    """The narrowed app policy (plan 04B) lets RunInstances create only resources tagged with this
    installation, and later changes (terminate) need the same tag."""
    p = _aws_provider()
    p._aws_config["aws_account_id"] = "123456789012"
    ec2 = MagicMock()
    ec2.describe_images.return_value = {"Images": [{"ImageId": "ami-new", "CreationDate": "2026-06-18T00:00:00Z"}]}
    ec2.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-0abc", "Placement": {"AvailabilityZone": "us-west-1a"}}]
    }
    with patch.object(p, "_client", return_value=ec2), patch.object(p, "_poll_vm_ready", new=AsyncMock()):
        await p.provision(_vm_spec())

    specs = {
        s["ResourceType"]: {t["Key"]: t["Value"] for t in s["Tags"]}
        for s in ec2.run_instances.call_args.kwargs["TagSpecifications"]
    }
    assert set(specs) == {"instance", "volume", "network-interface"}
    for resource_type, tags in specs.items():
        assert tags["bioaf:installation"] == "123456789012-us-west-1", resource_type
        assert tags["bioaf-managed"] == "true"
    assert specs["instance"]["bioaf-session"] == "7"
