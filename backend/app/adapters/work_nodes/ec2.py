"""EC2 work node adapter (cleanup item 8b, the AWS analog of gce.py).

Manages EC2 instances for SSH-accessible work nodes, the AWS sibling of
``GceVmInstance`` / ``GCEWorkNodeProvider``. The work-node image is an AMI built
by the Packer ``amazon-ebs`` CodeBuild path (see ``environment_build_service``);
its ``image_uri`` is the deterministic AMI *name*, which this adapter resolves to
an AMI id at launch.

Per-cloud mechanics that differ from GCE but are faithful, not regressions:
  - EC2 RunInstances (vs compute_v1 insert); instances launch into the EKS
    compute-module VPC's public subnet + a work-node security group, with a
    work-node IAM instance profile (S3 access), all from platform_config.
  - The startup script stages inputs / syncs outputs with ``aws s3`` (vs gsutil)
    and s3:// prefixes; the rest (PAM user, SSH, GitHub clone, heartbeat,
    shutdown-sync) is identical to the GCE script.

boto3 lives behind this adapter boundary (the VM authenticates ambiently through
its instance profile, exactly like ``S3StorageProvider`` / the ECR/CodeBuild
providers); no explicit credentials object is threaded.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from app.adapters.base import VmInstance, WorkNodeProvider
from app.adapters.capabilities import ProviderCapabilities
from app.adapters.models import (
    StoredObject,
    TerminationResult,
    VmInfo,
    VmStatus,
    to_service_state,
)
from app.exceptions import ValidationError

logger = logging.getLogger("bioaf.adapters.work_nodes.ec2")

# In-memory session store for local mode
_local_vms: dict[str, dict] = {}

# EC2 instance state -> the neutral status strings the work-node service expects
# (the same vocabulary the GCE adapter emits).
_EC2_STATE_MAP = {
    "pending": "starting",
    "running": "running",
    "shutting-down": "stopping",
    "stopping": "stopping",
    "stopped": "stopped",
    "terminated": "stopped",
}


def _build_ec2_startup_script(vm_spec: dict) -> str:
    """Build the EC2 user-data startup script from a work node spec.

    Mirrors the GCE ``_build_startup_script``: the Packer-built AMI already has
    conda, sshd, awscli, and system packages; this configures the user session.
    Object staging uses ``aws s3`` + s3:// prefixes instead of gsutil + gs://.
    """
    creds = vm_spec.get("session_credentials", {})
    username = creds.get("username", "bioaf")
    password_hash = creds.get("password_hash", "")
    home_dir = f"/home/{username}"

    ssh_private_key = vm_spec.get("ssh_private_key", "")
    ssh_public_key = vm_spec.get("ssh_public_key", "")
    heartbeat_token = vm_spec.get("heartbeat_token", "")
    github_repos = vm_spec.get("github_repos", [])
    input_files = vm_spec.get("input_files", [])
    working_bucket = vm_spec.get("working_bucket", "")
    session_id = vm_spec.get("session_id", 0)
    env_name = vm_spec.get("conda_env_name", "base")
    env_label = vm_spec.get("environment_label", "")

    lines = [
        "#!/bin/bash",
        "# Log all output for debugging",
        "exec > >(tee -a /var/log/bioaf-startup.log) 2>&1",
        "",
        "# 1. Create PAM user with session credentials",
        f"useradd -m -d {home_dir} -s /bin/bash {username} || true",
    ]

    if password_hash.startswith("$2"):
        lines.append(f"echo '{username}:{password_hash}' | chpasswd -e")
    else:
        lines.append(f"echo '{username}:{password_hash}' | chpasswd")

    # 2. SSH keys for GitHub
    if ssh_private_key:
        escaped_key = ssh_private_key.replace("'", "'\\''")
        lines += [
            "",
            "# 2. SSH keys for GitHub",
            f"mkdir -p {home_dir}/.ssh",
            f"printf '%s\\n' '{escaped_key}' > {home_dir}/.ssh/id_rsa",
            f"chmod 600 {home_dir}/.ssh/id_rsa",
            f"ssh-keyscan github.com >> {home_dir}/.ssh/known_hosts 2>/dev/null",
        ]
        if ssh_public_key:
            escaped_pub = ssh_public_key.replace("'", "'\\''")
            lines.append(f"printf '%s\\n' '{escaped_pub}' > {home_dir}/.ssh/id_rsa.pub")
        lines.append(f"chown -R {username}:{username} {home_dir}/.ssh")

    # 3. Clone GitHub repos
    if github_repos:
        lines += [
            "",
            "# 3. Clone GitHub repos",
            f"mkdir -p {home_dir}/repos",
        ]
        for repo in github_repos:
            url = repo["git_ssh_url"]
            name = repo["display_name"]
            safe_name = name.replace("'", "'\\''")
            safe_url = url.replace("'", "'\\''")
            lines.append(
                f"cd {home_dir}/repos && "
                f"GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=no -i {home_dir}/.ssh/id_rsa' "
                f"git clone '{safe_url}' '{safe_name}' || "
                f"echo 'Warning: failed to clone {safe_name}'"
            )
        lines.append(f"chown -R {username}:{username} {home_dir}/repos")

    # 4. Copy input files from object storage (S3)
    if input_files:
        lines += [
            "",
            "# 4. Copy input files from S3",
            "mkdir -p /data",
        ]
        for input_file in input_files:
            rel_path = input_file["relative_path"]
            object_uri = input_file["gcs_uri"]  # value is the file's storage_uri (s3:// on AWS)
            dest_path = f"/data/{rel_path}"
            dest_dir = "/".join(dest_path.split("/")[:-1])
            lines.append(
                f"mkdir -p '{dest_dir}' && "
                f"aws s3 cp '{object_uri}' '{dest_path}' || "
                f"echo 'Warning: failed to copy {rel_path}'"
            )

    # 5. Create output and scratch directories
    lines += [
        "",
        "# 5. Create output and scratch directories",
        "mkdir -p /outputs /scratch",
        f"chown {username}:{username} /outputs /scratch",
    ]

    # 6. Create bioaf-sync user for backend SSH access (output sync at stop)
    sync_public_key = vm_spec.get("sync_public_key", "")
    if sync_public_key:
        lines += [
            "",
            "# 6. Create bioaf-sync user for output sync",
            "useradd -r -m -s /bin/bash bioaf-sync || true",
            "mkdir -p /home/bioaf-sync/.ssh",
            f"echo '{sync_public_key}' > /home/bioaf-sync/.ssh/authorized_keys",
            "chmod 700 /home/bioaf-sync/.ssh",
            "chmod 600 /home/bioaf-sync/.ssh/authorized_keys",
            "chown -R bioaf-sync:bioaf-sync /home/bioaf-sync/.ssh",
            "# Allow bioaf-sync to read /outputs/ and run aws s3",
            "usermod -aG root bioaf-sync 2>/dev/null || true",
        ]

    # 7. Install shutdown sync service (fallback for unclean stops)
    if working_bucket and session_id:
        s3_output_prefix = f"s3://{working_bucket}/sessions/{session_id}/outputs/"
        s3_scripts_prefix = f"s3://{working_bucket}/sessions/{session_id}/scripts/"
        lines += [
            "",
            "# 7. Install shutdown sync service",
            "cat > /usr/local/bin/bioaf-shutdown-sync.sh << 'SYNCEOF'",
            "#!/bin/bash",
            'if [ -d /outputs ] && [ "$(ls -A /outputs)" ]; then',
            f"  aws s3 sync /outputs {s3_output_prefix}",
            "fi",
            f"find /home -maxdepth 4 "
            r"\( -name '*.ipynb' -o -name '*.Rmd' -o -name '*.R' -o -name '*.py' \) "
            f"-type f "
            f'| while read f; do aws s3 cp "$f" '
            f'{s3_scripts_prefix}"$(basename "$f")"; done',
            "SYNCEOF",
            "chmod +x /usr/local/bin/bioaf-shutdown-sync.sh",
            "",
            "cat > /etc/systemd/system/bioaf-shutdown-sync.service << 'SVCEOF'",
            "[Unit]",
            "Description=bioAF output sync on shutdown",
            "DefaultDependencies=no",
            "Before=shutdown.target reboot.target halt.target",
            "",
            "[Service]",
            "Type=oneshot",
            "ExecStart=/usr/local/bin/bioaf-shutdown-sync.sh",
            "TimeoutStartSec=300",
            "",
            "[Install]",
            "WantedBy=halt.target reboot.target shutdown.target",
            "SVCEOF",
            "systemctl daemon-reload",
            "systemctl enable bioaf-shutdown-sync.service",
        ]

    # 8. Activate conda env in user's shell
    if env_name and env_name != "base":
        lines += [
            "",
            "# 8. Activate conda environment",
            f"echo 'source /opt/conda/etc/profile.d/conda.sh && conda activate {env_name}' >> {home_dir}/.bashrc",
        ]

    # 9. Generate MOTD
    repo_names = ", ".join(r["display_name"] for r in github_repos) if github_repos else "(none)"
    file_count = len(input_files)
    lines += [
        "",
        "# 9. Generate MOTD",
        "cat > /etc/motd << 'MOTD_EOF'",
        "",
        "=== bioAF Work Node ===",
        "",
        "  Input data:     /data/                    (copied from S3 at boot)",
        f"  Your repos:     {home_dir}/repos/          (cloned from GitHub)",
        "  Output files:   /outputs/                  (synced to S3 on stop)",
        "  Scratch space:  /scratch/                  (LOST on stop)",
        "",
        f"  Environment:    {env_label}",
        f"  Repos:          {repo_names}",
        f"  Input files:    {file_count} file(s)",
        "",
        "MOTD_EOF",
    ]

    # 10. Heartbeat agent
    if heartbeat_token:
        lines += [
            "",
            "# 10. Heartbeat agent",
            "mkdir -p /etc/bioaf",
            f"echo '{heartbeat_token}' > /etc/bioaf/token",
        ]
        api_base = vm_spec.get("api_base_url", "")
        if api_base:
            lines += [
                f"echo '*/5 * * * * curl -s -X POST {api_base}/api/v1/work-nodes/sessions/{session_id}/heartbeat "
                f'-H "X-Heartbeat-Token: {heartbeat_token}" > /dev/null 2>&1\' | crontab -',
            ]

    # 11. Ownership
    lines += [
        "",
        "# 11. Final ownership",
        f"chown -R {username}:{username} {home_dir}",
    ]

    return "\n".join(lines)


class Ec2VmInstance(VmInstance):
    """EC2 VM lifecycle backend (boto3), with local mode for development.

    Implements the cloud-neutral VmInstance primitive (provision/delete/inspect/
    list) for EC2 and carries the work-node session orchestration (readiness
    poll, SSH output sync, DB session updates). ``Ec2WorkNodeProvider`` rides on
    this under the WorkNodeProvider names, exactly as GCE does.
    """

    def __init__(self, session_factory=None):
        self._mode = os.environ.get("BIOAF_COMPUTE_MODE", "local")
        self._session_factory = session_factory
        self._aws_config: dict | None = None
        # Sync SSH private keys keyed by session_id (generated at launch, used at
        # terminate to SSH in for output sync), mirroring the GCE adapter.
        self._sync_keys: dict[int, str] = {}

    @property
    def is_local(self) -> bool:
        return self._mode == "local"

    async def load_config(self, force: bool = False) -> dict:
        """Read AWS work-node config from platform_config. Caches the result.

        The neutral counterpart of the GCE adapter's ``load_gcp_config`` (the
        registry calls ``load_config`` on whichever work-node backend is active).
        """
        if self._aws_config is not None and not force:
            return self._aws_config

        if not self._session_factory:
            self._aws_config = {}
            return self._aws_config

        async with self._session_factory() as session:
            from app.platform.platform_config_service import PlatformConfigService

            self._aws_config = await PlatformConfigService.get_many(
                session,
                [
                    "aws_region",
                    "working_bucket_name",
                    "aws_work_node_subnet_id",
                    "aws_work_node_security_group_id",
                    "aws_work_node_instance_profile",
                ],
            )

        return self._aws_config

    def _client(self, service: str):
        """Construct a boto3 client (ambient instance-profile credentials)."""
        import boto3

        cfg = self._aws_config or {}
        return boto3.client(service, region_name=(cfg.get("aws_region") or None))

    async def provision(self, vm_spec: dict) -> VmInfo:
        if self.is_local:
            return self._local_launch_vm(vm_spec)
        return await self._ec2_launch_vm(vm_spec)

    async def delete(self, instance_name: str, zone: str, **kwargs) -> TerminationResult:
        if self.is_local:
            return self._local_terminate_vm(instance_name)
        return await self._ec2_terminate_vm(instance_name, zone, **kwargs)

    async def inspect(self, instance_name: str, zone: str) -> VmStatus:
        if self.is_local:
            return self._local_get_vm_status(instance_name)
        return await self._ec2_get_vm_status(instance_name, zone)

    async def list_instances(self, filters: dict | None = None) -> list[VmStatus]:
        if self.is_local:
            return self._local_list_vms(filters)
        return await self._ec2_list_vms(filters)

    # -- EC2 API implementations --

    def _clean(self, value) -> str:
        return value if value and value != "null" else ""

    def _resolve_ami_id(self, ec2, image_uri: str) -> str:
        """Resolve a deterministic AMI name to its current AMI id (most recent)."""
        # image_uri is the AMI name the Packer build set (bioaf-worknode-...). If a
        # raw ami-id was provided, pass it through.
        if image_uri.startswith("ami-"):
            return image_uri
        resp = ec2.describe_images(
            Owners=["self"],
            Filters=[
                {"Name": "name", "Values": [image_uri]},
                {"Name": "state", "Values": ["available"]},
            ],
        )
        images = sorted(resp.get("Images", []), key=lambda i: i.get("CreationDate", ""), reverse=True)
        if not images:
            raise ValidationError(f"Work-node image not found: {image_uri}. Build the environment image first.")
        return images[0]["ImageId"]

    async def _ec2_launch_vm(self, vm_spec: dict) -> VmInfo:
        """Create an EC2 instance for a work node."""
        await self.load_config()
        cfg = self._aws_config or {}

        subnet_id = self._clean(cfg.get("aws_work_node_subnet_id"))
        security_group_id = self._clean(cfg.get("aws_work_node_security_group_id"))
        instance_profile = self._clean(cfg.get("aws_work_node_instance_profile"))
        if not subnet_id or not security_group_id:
            raise ValidationError("Work-node networking is not deployed. Deploy compute infrastructure first.")

        session_id = vm_spec.get("session_id", 0)
        user_id = vm_spec.get("user_id", 0)
        instance_type = vm_spec.get("ec2_instance_type") or vm_spec.get("machine_type", "m5.xlarge")
        image_uri = vm_spec.get("image_uri", "")
        if not image_uri:
            raise ValidationError("No image_uri provided for work node")

        boot_disk_gb = vm_spec.get("boot_disk_gb", 100)
        instance_label = f"bioaf-worknode-{session_id}"

        # Generate an SSH key pair for the bioaf-sync user so the backend can SSH
        # into the VM at terminate time to sync outputs (same as GCE).
        import asyncssh

        sync_key = asyncssh.generate_private_key("ssh-ed25519")
        self._sync_keys[session_id] = sync_key.export_private_key().decode()
        vm_spec["sync_public_key"] = sync_key.export_public_key().decode().strip()

        startup_script = _build_ec2_startup_script(vm_spec)

        ec2 = self._client("ec2")
        ami_id = await asyncio.to_thread(self._resolve_ami_id, ec2, image_uri)

        run_kwargs: dict = {
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": startup_script,
            "BlockDeviceMappings": [
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {
                        "VolumeSize": boot_disk_gb,
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                    },
                }
            ],
            # A public IP regardless of the subnet default, so the operator/users
            # can reach SSH (the AWS analog of the GCE external NAT).
            "NetworkInterfaces": [
                {
                    "DeviceIndex": 0,
                    "SubnetId": subnet_id,
                    "Groups": [security_group_id],
                    "AssociatePublicIpAddress": True,
                    "DeleteOnTermination": True,
                }
            ],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": instance_label},
                        {"Key": "bioaf-session", "Value": str(session_id)},
                        {"Key": "bioaf-user", "Value": str(user_id)},
                        {"Key": "bioaf-managed", "Value": "true"},
                    ],
                }
            ],
        }
        if instance_profile:
            run_kwargs["IamInstanceProfile"] = {"Name": instance_profile}

        result = await asyncio.to_thread(lambda: ec2.run_instances(**run_kwargs))
        instance = result["Instances"][0]
        instance_id = instance["InstanceId"]
        az = instance.get("Placement", {}).get("AvailabilityZone", "")
        logger.info("Launched EC2 work node %s (%s) in %s", instance_id, instance_type, az)

        # Background readiness poll
        creds = vm_spec.get("session_credentials", {})
        ssh_username = creds.get("username", "")
        asyncio.create_task(self._poll_vm_ready(session_id, instance_id, ssh_username))

        return VmInfo(
            instance_name=instance_id,
            status=to_service_state("starting"),
            zone=az,
            access_url=None,
            provider_details={"image_id": ami_id},
        )

    async def _poll_vm_ready(self, session_id: int, instance_id: str, ssh_username: str = "") -> None:
        """Background: poll for running status + public IP, then update the DB."""
        try:
            ec2 = self._client("ec2")
            public_ip = None
            last_state = None
            for _ in range(60):  # up to 5 minutes
                try:
                    resp = await asyncio.to_thread(lambda: ec2.describe_instances(InstanceIds=[instance_id]))
                    inst = resp["Reservations"][0]["Instances"][0]
                    state = inst.get("State", {}).get("Name", "")
                    last_state = state
                    if state == "running":
                        public_ip = inst.get("PublicIpAddress")
                        if public_ip:
                            break
                    elif state in ("terminated", "stopped", "shutting-down"):
                        logger.error("EC2 work node %s entered %s", instance_id, state)
                        await self._update_session_in_db(
                            session_id,
                            status="failed",
                            access_url=None,
                            failure_message=f"Instance entered {state} state before becoming ready.",
                        )
                        return
                except Exception:
                    pass
                await asyncio.sleep(5)

            if not public_ip:
                logger.error("EC2 work node %s not running with a public IP after 5 min", instance_id)
                await self._update_session_in_db(
                    session_id,
                    status="failed",
                    access_url=None,
                    failure_message=f"Instance did not become reachable within 5 minutes (last state: {last_state}).",
                )
                return

            user_prefix = f"{ssh_username}@" if ssh_username else ""
            access_url = f"ssh://{user_prefix}{public_ip}:22"
            logger.info("EC2 work node %s ready at %s", instance_id, public_ip)
            await self._update_session_in_db(session_id, status="running", access_url=access_url)
        except Exception as e:
            from app.adapters.failure_classification import FAILURE_REASON_UNKNOWN

            logger.exception("Background poll failed for EC2 work node session %s", session_id)
            await self._update_session_in_db(
                session_id,
                status="failed",
                access_url=None,
                failure_reason=FAILURE_REASON_UNKNOWN,
                failure_message=f"Background poll raised: {e}",
            )

    async def _update_session_in_db(
        self,
        session_id: int,
        status: str,
        access_url: str | None,
        failure_reason: str | None = None,
        failure_message: str | None = None,
    ) -> None:
        """Update a work node session's status / access_url / failure fields.

        Identical contract to the GCE adapter's updater; only writes the failure
        columns when set so a service-layer explanation is not clobbered.
        """
        if not self._session_factory:
            logger.warning("No session_factory, cannot update session %s", session_id)
            return

        try:
            async with self._session_factory() as db:
                from sqlalchemy import text

                if failure_reason is not None or failure_message is not None:
                    await db.execute(
                        text(
                            "UPDATE compute_sessions "
                            "SET status = :status, access_url = :url, "
                            "    failure_reason = :reason, failure_message = :msg "
                            "WHERE id = :id"
                        ),
                        {
                            "status": status,
                            "url": access_url,
                            "reason": failure_reason,
                            "msg": failure_message,
                            "id": session_id,
                        },
                    )
                else:
                    await db.execute(
                        text("UPDATE compute_sessions SET status = :status, access_url = :url WHERE id = :id"),
                        {"status": status, "url": access_url, "id": session_id},
                    )
                await db.commit()
        except Exception:
            logger.exception("Failed to update session %s in DB", session_id)

    async def _ec2_terminate_vm(
        self,
        instance_name: str,
        zone: str,
        *,
        session_id: int = 0,
        working_bucket: str = "",
        **kwargs,
    ) -> TerminationResult:
        """Sync outputs from the instance over SSH, then terminate it."""
        await self.load_config()
        ec2 = self._client("ec2")

        output_files: list[StoredObject] = []
        s3_output_prefix = ""

        # Sync outputs from the running instance before terminating it (SSH in as
        # bioaf-sync, run aws s3 sync), mirroring the GCE flow.
        if working_bucket and session_id:
            s3_output_prefix = f"s3://{working_bucket}/sessions/{session_id}/outputs/"
            s3_scripts_prefix = f"s3://{working_bucket}/sessions/{session_id}/scripts/"

            public_ip = None
            try:
                resp = await asyncio.to_thread(lambda: ec2.describe_instances(InstanceIds=[instance_name]))
                public_ip = resp["Reservations"][0]["Instances"][0].get("PublicIpAddress")
            except Exception as e:
                logger.warning("Could not get public IP for EC2 work node %s: %s", instance_name, e)

            if public_ip:
                sync_cmd = (
                    f'if [ -d /outputs ] && [ "$(ls -A /outputs)" ]; then '
                    f"aws s3 sync /outputs {s3_output_prefix}; fi; "
                    f"find /home -maxdepth 4 "
                    r"\( -name '*.ipynb' -o -name '*.Rmd' -o -name '*.R' -o -name '*.py' \) "
                    f"-type f "
                    f'| while read f; do aws s3 cp "$f" '
                    f'{s3_scripts_prefix}"$(basename "$f")"; done'
                )
                try:
                    import asyncssh

                    sync_key = self._sync_keys.get(session_id)
                    if sync_key:
                        key = asyncssh.import_private_key(sync_key)
                        async with asyncssh.connect(
                            public_ip,
                            port=22,
                            username="bioaf-sync",
                            client_keys=[key],
                            known_hosts=None,
                        ) as conn:
                            res = await asyncio.wait_for(conn.run(sync_cmd), timeout=300)
                            if res.exit_status == 0:
                                logger.info("Output sync complete for EC2 work node %s", instance_name)
                            else:
                                logger.warning(
                                    "Output sync returned %s for %s: %s",
                                    res.exit_status,
                                    instance_name,
                                    res.stderr,
                                )
                    else:
                        logger.warning("No sync key for session %d, skipping output sync", session_id)
                except Exception as e:
                    logger.warning("SSH output sync failed for EC2 work node %s: %s", instance_name, e)

        # Terminate the instance.
        try:
            await asyncio.to_thread(lambda: ec2.terminate_instances(InstanceIds=[instance_name]))
            logger.info("Terminated EC2 work node %s", instance_name)
        except Exception as e:
            logger.warning("Failed to terminate EC2 work node %s: %s", instance_name, e)

        self._sync_keys.pop(session_id, None)

        # List the synced output objects from S3.
        if working_bucket and session_id:
            try:
                s3 = self._client("s3")
                prefix = f"sessions/{session_id}/"
                paginator = s3.get_paginator("list_objects_v2")
                for page in await asyncio.to_thread(
                    lambda: list(paginator.paginate(Bucket=working_bucket, Prefix=prefix))
                ):
                    for obj in page.get("Contents", []):
                        key = obj["Key"]
                        output_files.append(
                            StoredObject(
                                filename=key.split("/")[-1],
                                storage_uri=f"s3://{working_bucket}/{key}",
                                size_bytes=obj.get("Size", 0),
                            )
                        )
            except Exception as e:
                logger.warning("Failed to list output files for session %s: %s", session_id, e)

        return TerminationResult(
            status=to_service_state("stopped"),
            output_files=output_files,
            output_prefix=s3_output_prefix,
            provider_details={"instance_name": instance_name, "stopped_at": datetime.now(timezone.utc).isoformat()},
        )

    async def _ec2_get_vm_status(self, instance_name: str, zone: str) -> VmStatus:
        ec2 = self._client("ec2")
        try:
            resp = await asyncio.to_thread(lambda: ec2.describe_instances(InstanceIds=[instance_name]))
            inst = resp["Reservations"][0]["Instances"][0]
            state = inst.get("State", {}).get("Name", "")
            return VmStatus(
                instance_name=instance_name,
                status=to_service_state(_EC2_STATE_MAP.get(state, "unknown")),
                zone=inst.get("Placement", {}).get("AvailabilityZone", zone),
                external_ip=inst.get("PublicIpAddress"),
            )
        except Exception:
            return VmStatus(instance_name=instance_name, status=to_service_state("unknown"), zone=zone)

    async def _ec2_list_vms(self, filters: dict | None = None) -> list[VmStatus]:
        ec2 = self._client("ec2")
        try:
            resp = await asyncio.to_thread(
                lambda: ec2.describe_instances(
                    Filters=[
                        {"Name": "tag:bioaf-managed", "Values": ["true"]},
                        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
                    ]
                )
            )
            vms: list[VmStatus] = []
            for reservation in resp.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                    vms.append(
                        VmStatus(
                            instance_name=inst["InstanceId"],
                            status=to_service_state(
                                _EC2_STATE_MAP.get(inst.get("State", {}).get("Name", ""), "unknown")
                            ),
                            zone=inst.get("Placement", {}).get("AvailabilityZone", ""),
                            external_ip=inst.get("PublicIpAddress"),
                            session_id=tags.get("bioaf-session", ""),
                            user_id=tags.get("bioaf-user", ""),
                        )
                    )
            return vms
        except Exception:
            logger.exception("Failed to list EC2 work nodes")
            return []

    # -- Local mode implementations --

    def _local_launch_vm(self, vm_spec: dict) -> VmInfo:
        instance_name = f"bioaf-worknode-local-{uuid.uuid4().hex[:8]}"
        _local_vms[instance_name] = {
            "instance_name": instance_name,
            "status": "running",
            "access_url": "ssh://127.0.0.1:22",
            "zone": "us-west-1a",
        }
        logger.info("Local mode: launched EC2 work node %s", instance_name)
        return VmInfo(
            instance_name=instance_name,
            status=to_service_state("running"),
            zone="us-west-1a",
            access_url="ssh://127.0.0.1:22",
        )

    def _local_terminate_vm(self, instance_name: str) -> TerminationResult:
        _local_vms.pop(instance_name, None)
        logger.info("Local mode: terminated EC2 work node %s", instance_name)
        return TerminationResult(status=to_service_state("stopped"), provider_details={"instance_name": instance_name})

    def _local_get_vm_status(self, instance_name: str) -> VmStatus:
        d = _local_vms.get(instance_name)
        if d:
            return VmStatus(
                instance_name=instance_name,
                status=to_service_state(d.get("status", "unknown")),
                zone=d.get("zone"),
            )
        return VmStatus(instance_name=instance_name, status=to_service_state("unknown"))

    def _local_list_vms(self, filters: dict | None = None) -> list[VmStatus]:
        return [
            VmStatus(
                instance_name=d["instance_name"],
                status=to_service_state(d.get("status", "unknown")),
                zone=d.get("zone"),
            )
            for d in _local_vms.values()
        ]


class Ec2WorkNodeProvider(Ec2VmInstance, WorkNodeProvider):
    """EC2 work-node adapter: an ``Ec2VmInstance`` exposed under the
    ``WorkNodeProvider`` interface the service layer and registry consume (the
    AWS sibling of ``GCEWorkNodeProvider``)."""

    def capabilities(self) -> ProviderCapabilities:
        """EC2 provides on-demand work-node VMs."""
        return ProviderCapabilities(work_nodes=True)

    async def launch_vm(self, vm_spec: dict) -> VmInfo:
        return await self.provision(vm_spec)

    async def terminate_vm(self, instance_name: str, zone: str, **kwargs) -> TerminationResult:
        return await self.delete(instance_name, zone, **kwargs)

    async def get_vm_status(self, instance_name: str, zone: str) -> VmStatus:
        return await self.inspect(instance_name, zone)

    async def list_vms(self, filters: dict | None = None) -> list[VmStatus]:
        return await self.list_instances(filters)
