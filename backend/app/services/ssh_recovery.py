"""Application-host SSH recovery: Restore SSH access, protected cloud recovery and the optional filter.

Plan 03B (D03-9). The backend never opens an operator shell, delivers a key or administers IAM.

- **Restore SSH access** is a read-only link to this exact server's cloud console page, built from
  the recorded immutable binding and a fixed provider origin, with the path-specific instruction.
  The human's own cloud account does the sign-in; no bioAF credential is used.
- **Cloud recovery access** (GCP IAP TCP 22 from 35.235.240.0/20 at priority 850; AWS EC2
  Instance Connect managed prefix lists or a prepared endpoint) is kept outside the editable list.
- The **host filter** restricts SSH only, never HTTPS/API; see ``host_filter``.

Host identity is discovered from the provider metadata service and reconciled with the recorded
binding by immutable instance id; a replacement is recorded as such, never trusted by name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone

HOST_IDENTITY_KEY = "app_host_identity"
GCP_IAP_SOURCE = "35.235.240.0/20"
GCP_IAP_PRIORITY = 850
GCP_FILTER_ALLOW_PRIORITY = 875
GCP_FILTER_DENY_PRIORITY = 1000

_GCP_NAME = re.compile(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?")
_GCP_PROJECT = re.compile(r"[a-z][-a-z0-9]{4,28}[a-z0-9]|[a-z0-9.-]+:[a-z][-a-z0-9]{4,28}[a-z0-9]")
_ZONE = re.compile(r"[a-z]+(-[a-z0-9]+)+-[a-z]")
_REGION = re.compile(r"[a-z]{2}(-[a-z]+)+-[0-9]+|[a-z]+-[a-z]+[0-9]+")
_AWS_INSTANCE = re.compile(r"i-[0-9a-f]{8,17}")
_AWS_ACCOUNT = re.compile(r"[0-9]{12}")
_AWS_ENDPOINT = re.compile(r"eice-[0-9a-f]{8,17}")
_OS_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_AWS_CONSOLE_ORIGINS = {
    "aws": "https://{region}.console.aws.amazon.com",
    "aws-cn": "https://{region}.console.amazonaws.cn",
    "aws-us-gov": "https://{region}.console.amazonaws-us-gov.com",
}

GCP_PRIVATE_INSTRUCTION = "Sign in to the Google Cloud account used for this server, then choose SSH."
GCP_PUBLIC_INSTRUCTION = "Open Cloud Shell on this server's page, paste the recovery command below, and press Enter."
AWS_INSTRUCTION = (
    "Sign in to the AWS account used for this server, choose Connect, then EC2 Instance Connect and Connect."
)
RECOVERY_CAPTION = "Cloud recovery access is always allowed. Your cloud-account permissions are still required."


class RecoveryError(Exception):
    """The host identity is unknown or unsafe to put in a link. The message names no secret."""


@dataclass(frozen=True)
class HostIdentity:
    provider: str
    account: str
    region: str
    zone: str
    instance_id: str
    instance_name: str
    public: bool
    os_username: str = ""
    partition: str = "aws"
    eic_endpoint_id: str = ""
    network: str = ""
    internal_ip: str = ""


@dataclass(frozen=True)
class Reconciled:
    identity: HostIdentity
    replaced: bool
    previous_instance_id: str | None = None


def _require(pattern: re.Pattern, value: str, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise RecoveryError(f"the recorded {what} is not valid")
    return value


def restore_access(identity: HostIdentity) -> dict:
    """The console URL, instruction and (public GCP) Cloud Shell command for this exact server."""
    server = {
        "provider": identity.provider,
        "account": identity.account,
        "zone": identity.zone,
        "instance": identity.instance_name,
        "instance_id": identity.instance_id,
    }
    if identity.provider == "gcp":
        project = _require(_GCP_PROJECT, identity.account, "project")
        zone = _require(_ZONE, identity.zone, "zone")
        name = _require(_GCP_NAME, identity.instance_name, "instance name")
        url = (
            f"https://console.cloud.google.com/compute/instancesDetail/zones/{zone}/instances/{name}?project={project}"
        )
        if identity.public:
            # The console's SSH button on a VM with an external IP does not use IAP; Cloud Shell does.
            return {
                "url": url,
                "instruction": GCP_PUBLIC_INSTRUCTION,
                "command": f"gcloud compute ssh {name} --project={project} --zone={zone} --tunnel-through-iap",
                "os_username": identity.os_username or None,
                "server": server,
            }
        return {
            "url": url,
            "instruction": GCP_PRIVATE_INSTRUCTION,
            "command": None,
            "os_username": identity.os_username or None,
            "server": server,
        }
    if identity.provider == "aws":
        region = _require(_REGION, identity.region, "region")
        instance = _require(_AWS_INSTANCE, identity.instance_id, "instance id")
        _require(_AWS_ACCOUNT, identity.account, "account")
        username = _require(_OS_USER, identity.os_username or "ubuntu", "operator username")
        origin = _AWS_CONSOLE_ORIGINS.get(identity.partition)
        if origin is None:
            raise RecoveryError("the recorded AWS partition is not supported")
        url = f"{origin.format(region=region)}/ec2/home?region={region}#ConnectToInstance:instanceId={instance}"
        link = {
            "url": url,
            "instruction": AWS_INSTRUCTION,
            "command": None,
            "os_username": username,
            "address_choice": "Public IP" if identity.public else "Private IP",
            "server": server,
        }
        if not identity.public:
            link["endpoint_id"] = _require(_AWS_ENDPOINT, identity.eic_endpoint_id, "EC2 Instance Connect endpoint")
        return link
    raise RecoveryError("this server is not on a supported cloud")


# -- Host identity -----------------------------------------------------------------------------


async def _metadata_identity() -> HostIdentity:
    """Discover this VM from its provider metadata service (GCP first, then AWS IMDSv2)."""
    import httpx

    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            headers = {"Metadata-Flavor": "Google"}
            base = "http://metadata.google.internal/computeMetadata/v1"
            project = (await client.get(f"{base}/project/project-id", headers=headers)).raise_for_status().text
            zone = (
                (await client.get(f"{base}/instance/zone", headers=headers)).raise_for_status().text.rsplit("/", 1)[-1]
            )
            instance_id = (await client.get(f"{base}/instance/id", headers=headers)).raise_for_status().text
            name = (await client.get(f"{base}/instance/name", headers=headers)).raise_for_status().text
            nat = await client.get(
                f"{base}/instance/network-interfaces/0/access-configs/0/external-ip", headers=headers
            )
            internal = (await client.get(f"{base}/instance/network-interfaces/0/ip", headers=headers)).text
            network = (await client.get(f"{base}/instance/network-interfaces/0/network", headers=headers)).text
            return HostIdentity(
                provider="gcp",
                account=project,
                region=zone.rsplit("-", 1)[0],
                zone=zone,
                instance_id=instance_id,
                instance_name=name,
                public=nat.status_code == 200 and bool(nat.text.strip()),
                network=network.rsplit("/", 1)[-1],
                internal_ip=internal.strip(),
            )
        except (httpx.HTTPError, IndexError):
            pass
        try:
            token = (
                (
                    await client.put(
                        "http://169.254.169.254/latest/api/token",
                        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
                    )
                )
                .raise_for_status()
                .text
            )
            headers = {"X-aws-ec2-metadata-token": token}
            doc = (
                (await client.get("http://169.254.169.254/latest/dynamic/instance-identity/document", headers=headers))
                .raise_for_status()
                .json()
            )
            public_ip = await client.get("http://169.254.169.254/latest/meta-data/public-ipv4", headers=headers)
            partition = (
                await client.get("http://169.254.169.254/latest/meta-data/services/partition", headers=headers)
            ).text
            return HostIdentity(
                provider="aws",
                account=doc["accountId"],
                region=doc["region"],
                zone=doc["availabilityZone"],
                instance_id=doc["instanceId"],
                instance_name="bioaf",
                public=public_ip.status_code == 200,
                os_username="ubuntu",
                partition=partition.strip() or "aws",
                internal_ip=doc.get("privateIp", ""),
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise RecoveryError("bioAF could not identify the server it runs on") from exc


async def reconcile_host_identity(session, discovered: HostIdentity) -> Reconciled:
    """Record the discovered binding; a different immutable instance id is a replacement."""
    from app.platform.platform_config_service import PlatformConfigService

    raw = await PlatformConfigService.get(session, HOST_IDENTITY_KEY)
    recorded = None
    if raw:
        try:
            recorded = HostIdentity(**json.loads(raw)["identity"])
        except (ValueError, TypeError, KeyError):
            recorded = None
    # Settings the installer or an operator recorded (OS username, private endpoint) carry over.
    identity = discovered
    if recorded and recorded.instance_id == discovered.instance_id:
        identity = replace(
            discovered,
            os_username=discovered.os_username or recorded.os_username,
            eic_endpoint_id=discovered.eic_endpoint_id or recorded.eic_endpoint_id,
        )
    replaced = recorded is not None and recorded.instance_id != discovered.instance_id
    if recorded != identity:
        record = {"identity": identity.__dict__, "recorded_at": datetime.now(timezone.utc).isoformat()}
        if replaced:
            record["replaced_instance_id"] = recorded.instance_id
        await PlatformConfigService.set(session, HOST_IDENTITY_KEY, json.dumps(record))
        await session.commit()
    return Reconciled(
        identity=identity, replaced=replaced, previous_instance_id=recorded.instance_id if replaced else None
    )


async def current_host_identity(session) -> HostIdentity:
    from app.config import settings
    from app.settings_contract import is_simulated

    if is_simulated(settings.compute_mode):
        raise RecoveryError("a simulated installation has no cloud server")
    discovered = await _metadata_identity()
    if discovered.provider == "aws" and not discovered.public and not discovered.eic_endpoint_id:
        discovered = replace(discovered, eic_endpoint_id=await _aws_endpoint(discovered))
    return (await reconcile_host_identity(session, discovered)).identity


async def _aws_endpoint(identity: HostIdentity) -> str:
    """The prepared EC2 Instance Connect Endpoint in a private server's VPC, or "" (best effort)."""
    import asyncio

    try:
        import boto3

        ec2 = boto3.client("ec2", region_name=identity.region)
        instance = (await asyncio.to_thread(lambda: ec2.describe_instances(InstanceIds=[identity.instance_id])))[
            "Reservations"
        ][0]["Instances"][0]
        endpoints = (
            await asyncio.to_thread(
                lambda: ec2.describe_instance_connect_endpoints(
                    Filters=[{"Name": "vpc-id", "Values": [instance["VpcId"]]}]
                )
            )
        ).get("InstanceConnectEndpoints", [])
    except Exception:
        return ""
    ready = [e for e in endpoints if e.get("State") in (None, "create-complete")]
    return ready[0].get("InstanceConnectEndpointId", "") if ready else ""


async def restore_view(session) -> dict:
    try:
        identity = await current_host_identity(session)
        return {**restore_access(identity), "message": None}
    except RecoveryError:
        return {
            "url": None,
            "instruction": None,
            "command": None,
            "server": None,
            "message": "bioAF could not identify the server it runs on, so it cannot link to its console. "
            "Use the access sheet saved by the installer, or find the server in your cloud account.",
        }


async def application_host_view(session, org_id: int | None = None) -> dict:
    from app.services.host_ssh_access import view

    return {**await view(session, org_id), "restore": await restore_view(session), "recovery_caption": RECOVERY_CAPTION}


async def record_aws_app_network(session) -> None:
    """Record the AWS application VM's VPC and security group for private backend sync (plan 03B).

    Read from the running instance through the backend's own authority; bioAF's own SSH groups
    are never the application group. Best effort: without it the compute module keeps public sync.
    """
    from app.platform.platform_config_service import PlatformConfigService

    identity = await current_host_identity(session)
    if identity.provider != "aws":
        return
    import asyncio

    import boto3

    ec2 = boto3.client("ec2", region_name=identity.region)
    resp = await asyncio.to_thread(lambda: ec2.describe_instances(InstanceIds=[identity.instance_id]))
    instance = resp["Reservations"][0]["Instances"][0]
    groups = [g for g in instance.get("SecurityGroups", []) if not g.get("GroupName", "").startswith("bioaf-ssh-")]
    if not groups:
        return
    await PlatformConfigService.set(session, "aws_app_vpc_id", instance["VpcId"])
    await PlatformConfigService.set(session, "aws_app_security_group_id", groups[0]["GroupId"])
    await session.commit()
