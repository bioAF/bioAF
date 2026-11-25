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
import posixpath
import uuid
from datetime import datetime, timezone

from app.config import compute_mode_for
from app.settings_contract import ComputeMode, is_simulated
from app.adapters.base import VmInstance, WorkNodeProvider
from app.adapters.capabilities import ProviderCapabilities
from app.adapters.models import (
    StoredObject,
    TerminationResult,
    VmInfo,
    VmStatus,
    to_service_state,
)
from app.adapters.session_status import update_compute_session
from app.adapters.work_nodes.sshd_policy import sshd_policy_install_lines
from app.adapters.work_nodes.node_ssh import (
    STOP_DELETE_UNCONFIRMED,
    STOP_SAVE_FAILED,
    STOP_STATE_UNKNOWN,
    NodeSyncError,
    host_key_install_lines,
    run_sync_command,
    sync_outputs_command,
)
from app.exceptions import ValidationError
from app.platform.git_trust import (
    LEGACY_UNVERIFIED_OPTIONS,
    STRICT_OPTIONS,
    github_known_hosts,
    ssh_host,
    write_known_hosts_lines,
)
from app.platform.shell_args import ShellExpr, safe_relative_path, shell_join, shell_word
from app import runtime

logger = logging.getLogger("bioaf.adapters.work_nodes.ec2")

# In-memory session store for local mode
_local_vms: dict[str, dict] = {}

# Sentinel: the provider could not say whether an instance exists.
_UNKNOWN = object()

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


# The loop variable `find -exec sh -c '...' sh {} +` binds each found name to.
_EACH_FILE = ShellExpr('"$f"')


def _capture_each(scripts_prefix: str) -> str:
    """The per-file copy `find -exec sh -c` runs: each found name is an argument, never re-parsed."""
    copy = shell_join(["aws", "s3", "cp", _EACH_FILE, scripts_prefix])
    return f"for f do {copy} || exit 1; done"


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
    ]
    host_private_key = vm_spec.get("host_private_key", "")
    host_public_key = vm_spec.get("host_public_key", "")
    if host_private_key and host_public_key:
        lines += host_key_install_lines(host_private_key, host_public_key)
    lines += [
        "",
        "# 1. Create PAM user with session credentials",
        f"useradd -m -d {home_dir} -s /bin/bash {username} || true",
    ]

    if password_hash.startswith("$2"):
        lines.append(f"echo '{username}:{password_hash}' | chpasswd -e")
    else:
        lines.append(f"echo '{username}:{password_hash}' | chpasswd")

    # 1b. Account-scoped SSH login policy (plan 03B, D03-5): the scientist's password works from
    # anywhere without a Work Node range, only inside it with one; bioaf-sync is key-only. Replaces
    # the older global PasswordAuthentication override, which an AWS cloudimg default would beat and
    # which cannot express a range.
    ssh_policy = vm_spec.get("ssh_policy") or {}
    lines += sshd_policy_install_lines(username, list(ssh_policy.get("ranges") or []))

    # 2. SSH keys for GitHub
    if ssh_private_key:
        escaped_key = ssh_private_key.replace("'", "'\\''")
        lines += [
            "",
            "# 2. SSH keys for GitHub",
            f"mkdir -p {home_dir}/.ssh",
            f"printf '%s\\n' '{escaped_key}' > {home_dir}/.ssh/id_rsa",
            f"chmod 600 {home_dir}/.ssh/id_rsa",
        ]
        # Git server trust: shipped GitHub keys plus operator-approved hosts, never a key scan.
        lines += write_known_hosts_lines(
            f"{home_dir}/.ssh/known_hosts", vm_spec.get("git_known_hosts") or github_known_hosts()
        )
        if ssh_public_key:
            escaped_pub = ssh_public_key.replace("'", "'\\''")
            lines.append(f"printf '%s\\n' '{escaped_pub}' > {home_dir}/.ssh/id_rsa.pub")
        lines.append(f"chown -R {username}:{username} {home_dir}/.ssh")

    # 2b. The user's own login key (plan 03B, D03-6): only this user's public key, in their own
    # authorized_keys, with sshd's required ownership and permissions.
    login_public_key = (vm_spec.get("login_public_key") or "").strip()
    if login_public_key:
        safe_login = login_public_key.replace("'", "")
        lines += [
            "",
            "# 2b. The user's login key",
            f"mkdir -p {home_dir}/.ssh",
            f"printf '%s\\n' '{safe_login}' > {home_dir}/.ssh/authorized_keys",
            f"chmod 700 {home_dir}/.ssh",
            f"chmod 600 {home_dir}/.ssh/authorized_keys",
            f"chown -R {username}:{username} {home_dir}/.ssh",
        ]

    # 3. Clone GitHub repos
    if github_repos:
        lines += [
            "",
            "# 3. Clone GitHub repos",
            f"mkdir -p {home_dir}/repos",
        ]
        untrusted = set(vm_spec.get("git_untrusted_hosts") or [])
        strict = STRICT_OPTIONS.format(path=f"{home_dir}/.ssh/known_hosts")
        for repo in github_repos:
            url = repo["git_ssh_url"]
            name = repo["display_name"]
            safe_name = name.replace("'", "'\\''")
            safe_url = url.replace("'", "'\\''")
            host = ssh_host(url)
            # A custom host with no approved key keeps its previous behavior for this repository only
            # (plan 03C deferral; reported to operators). Everything else is verified.
            options = LEGACY_UNVERIFIED_OPTIONS if host and f"{host[0]}:{host[1]}" in untrusted else strict
            lines.append(
                f"cd {home_dir}/repos && "
                f"GIT_SSH_COMMAND='ssh {options} -i {home_dir}/.ssh/id_rsa' "
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
            # Every operand is one quoted literal (a filename is data, never shell), and the path
            # cannot leave /data. The value is the file's storage_uri (s3:// on AWS).
            rel_path = safe_relative_path(input_file["relative_path"])
            dest_path = f"/data/{rel_path}"
            lines.append(
                f"mkdir -p {shell_word(posixpath.dirname(dest_path))} && "
                + shell_join(["aws", "s3", "cp", input_file["gcs_uri"], dest_path])
                + f" || echo {shell_word('Warning: failed to copy ' + rel_path)}"
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
            f"-exec sh -c {shell_word(_capture_each(s3_scripts_prefix))} sh {{}} +",
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
                f'-H "X-Heartbeat-Token: {heartbeat_token}" '
                f'-H "X-SSH-Policy: $(cat /etc/bioaf/ssh-policy-status 2>/dev/null)" > /dev/null 2>&1\' | crontab -',
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

    def __init__(self, session_factory=None, *, mode: ComputeMode | None = None):
        self._mode = compute_mode_for(mode)
        self._session_factory = session_factory
        self._aws_config: dict | None = None
        # Deletion is confirmed by polling the instance state (seconds between polls, poll count).
        self._delete_poll_interval: float = 5
        self._delete_poll_attempts: int = 24

    @property
    def is_local(self) -> bool:
        return is_simulated(self._mode)

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

        # The service generated and committed the sync key and host identity before this call;
        # only their public halves (and the node's own host key) are in the spec.
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
                    "Groups": [security_group_id]
                    + (
                        [vm_spec["backend_sync_security_group"]]
                        if vm_spec.get("sync_route") == "private-v1" and vm_spec.get("backend_sync_security_group")
                        else []
                    ),
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
                        {"Key": "bioaf-launch", "Value": str(vm_spec.get("launch_attempt_id") or "")},
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
        runtime.spawn(
            self._poll_vm_ready(session_id, instance_id, ssh_username, organization_id=vm_spec.get("organization_id")),
            role="work-node-readiness",
        )

        return VmInfo(
            instance_name=instance_id,
            status=to_service_state("starting"),
            zone=az,
            access_url=None,
            provider_details={
                "image_id": ami_id,
                "provider_instance_id": instance_id,
                "aws_account_id": result.get("OwnerId", ""),
            },
        )

    async def _poll_vm_ready(
        self, session_id: int, instance_id: str, ssh_username: str = "", *, organization_id: int | None = None
    ) -> None:
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
                            organization_id,
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
                    organization_id,
                    session_id,
                    status="failed",
                    access_url=None,
                    failure_message=f"Instance did not become reachable within 5 minutes (last state: {last_state}).",
                )
                return

            user_prefix = f"{ssh_username}@" if ssh_username else ""
            access_url = f"ssh://{user_prefix}{public_ip}:22"
            logger.info("EC2 work node %s ready at %s", instance_id, public_ip)
            await self._update_session_in_db(organization_id, session_id, status="running", access_url=access_url)
        except Exception as e:
            from app.adapters.failure_classification import FAILURE_REASON_UNKNOWN

            logger.exception("Background poll failed for EC2 work node session %s", session_id)
            await self._update_session_in_db(
                organization_id,
                session_id,
                status="failed",
                access_url=None,
                failure_reason=FAILURE_REASON_UNKNOWN,
                failure_message=f"Background poll raised: {e}",
            )

    async def _update_session_in_db(
        self,
        organization_id: int | None,
        session_id: int,
        status: str,
        access_url: str | None,
        failure_reason: str | None = None,
        failure_message: str | None = None,
    ) -> None:
        """Record the poll's outcome on the session row (id AND organization; see
        ``app.adapters.session_status``). Failure fields are written only when set."""
        await update_compute_session(
            self._session_factory,
            organization_id=organization_id,
            session_id=session_id,
            status=status,
            access_url=access_url,
            failure_reason=failure_reason,
            failure_message=failure_message,
        )

    async def _ec2_terminate_vm(
        self,
        instance_name: str,
        zone: str,
        *,
        session_id: int = 0,
        working_bucket: str = "",
        sync_private_key: str | None = None,
        host_public_key: str | None = None,
        launch_attempt_id: str | None = None,
        sync_route: str | None = None,
        **kwargs,
    ) -> TerminationResult:
        """Save the node's outputs over verified SSH, then terminate it and confirm termination.

        The instance is terminated only after every required transfer succeeded. Unknown state,
        a binding mismatch, missing credentials/bucket/address, an untrusted server or a failed
        transfer keep the instance and report why (``TerminationResult.stop_failure``).
        """
        await self.load_config()
        ec2 = self._client("ec2")
        s3_output_prefix = f"s3://{working_bucket}/sessions/{session_id}/outputs/" if working_bucket else ""

        def kept(reason: str, state: str = "unknown") -> TerminationResult:
            logger.warning("Kept EC2 work node %s (session %s): %s", instance_name, session_id, reason)
            return TerminationResult(
                status=to_service_state(state),
                output_prefix=s3_output_prefix,
                provider_details={"instance_name": instance_name},
                stop_failure=reason,
            )

        inst = await self._describe_instance(ec2, instance_name)
        if inst is _UNKNOWN:
            return kept(STOP_STATE_UNKNOWN)
        if inst is not None:
            state = inst.get("State", {}).get("Name", "")
            if state != "terminated":
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                if session_id and tags.get("bioaf-session") != str(session_id):
                    return kept(STOP_STATE_UNKNOWN)
                if launch_attempt_id and tags.get("bioaf-launch") != launch_attempt_id:
                    return kept(STOP_STATE_UNKNOWN)
                if state == "shutting-down":
                    return kept(STOP_DELETE_UNCONFIRMED, "stopping")
                if state != "running":
                    return kept(STOP_STATE_UNKNOWN)
                if session_id:
                    # Private-route nodes are saved over the peered network (plan 03B).
                    public_ip = (
                        inst.get("PrivateIpAddress") if sync_route == "private-v1" else inst.get("PublicIpAddress")
                    )
                    if not (working_bucket and public_ip and sync_private_key and host_public_key):
                        return kept(STOP_SAVE_FAILED, "running")
                    command = sync_outputs_command(
                        f"aws s3 sync /outputs {s3_output_prefix}",
                        shell_word(_capture_each(f"s3://{working_bucket}/sessions/{session_id}/scripts/")),
                    )
                    try:
                        status = await run_sync_command(
                            public_ip,
                            sync_private_key=sync_private_key,
                            host_public_key=host_public_key,
                            command=command,
                        )
                    except NodeSyncError as exc:
                        logger.warning("Output sync for EC2 work node %s failed: %s", instance_name, exc)
                        return kept(STOP_SAVE_FAILED, "running")
                    if status != 0:
                        logger.warning("Output sync for EC2 work node %s exited %s", instance_name, status)
                        return kept(STOP_SAVE_FAILED, "running")
                    logger.info("Output sync complete for EC2 work node %s", instance_name)

                if not await self._terminate_and_confirm(ec2, instance_name):
                    return kept(STOP_DELETE_UNCONFIRMED, "running")

        output_files = await self._list_outputs(working_bucket, session_id) if working_bucket and session_id else []
        return TerminationResult(
            status=to_service_state("stopped"),
            output_files=output_files,
            output_prefix=s3_output_prefix,
            provider_details={"instance_name": instance_name, "stopped_at": datetime.now(timezone.utc).isoformat()},
        )

    async def _describe_instance(self, ec2, instance_id: str):
        """The instance dict, None when AWS confirms it does not exist, or _UNKNOWN."""
        try:
            resp = await asyncio.to_thread(lambda: ec2.describe_instances(InstanceIds=[instance_id]))
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
            if code == "InvalidInstanceID.NotFound":
                return None
            logger.warning("Could not describe EC2 work node %s: %s", instance_id, exc)
            return _UNKNOWN
        reservations = resp.get("Reservations", [])
        if not reservations or not reservations[0].get("Instances"):
            return None
        return reservations[0]["Instances"][0]

    async def _terminate_and_confirm(self, ec2, instance_id: str) -> bool:
        try:
            await asyncio.to_thread(lambda: ec2.terminate_instances(InstanceIds=[instance_id]))
        except Exception as exc:
            logger.warning("Failed to terminate EC2 work node %s: %s", instance_id, exc)
            return False
        for _ in range(self._delete_poll_attempts):
            inst = await self._describe_instance(ec2, instance_id)
            if inst is None or (inst is not _UNKNOWN and inst.get("State", {}).get("Name") == "terminated"):
                logger.info("Terminated EC2 work node %s", instance_id)
                return True
            await asyncio.sleep(self._delete_poll_interval)
        return False

    async def _list_outputs(self, working_bucket: str, session_id: int) -> list[StoredObject]:
        output_files: list[StoredObject] = []
        try:
            s3 = self._client("s3")
            prefix = f"sessions/{session_id}/"
            paginator = s3.get_paginator("list_objects_v2")
            for page in await asyncio.to_thread(lambda: list(paginator.paginate(Bucket=working_bucket, Prefix=prefix))):
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
        return output_files

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

    async def inventory_instances(self) -> list:
        """Every existing (not terminated) bioAF Work Node instance in the Region, including stopped
        ones whose volumes remain. Raises InventoryUnavailable."""
        from app.platform.work_node_inventory import InventoryUnavailable, ManagedInstance

        if self.is_local:
            return []
        try:
            await self.load_config()
            ec2 = self._client("ec2")
            paginator = ec2.get_paginator("describe_instances")
            filters = [
                {"Name": "tag:bioaf-managed", "Values": ["true"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "shutting-down", "stopping", "stopped"],
                },
            ]
            pages = await asyncio.to_thread(lambda: list(paginator.paginate(Filters=filters)))
        except Exception as exc:
            raise InventoryUnavailable(type(exc).__name__) from exc
        found = []
        for page in pages:
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                    found.append(
                        ManagedInstance(
                            name=inst["InstanceId"],
                            session_id=tags.get("bioaf-session"),
                            zone=inst.get("Placement", {}).get("AvailabilityZone") or None,
                            provider_instance_id=inst["InstanceId"],
                            launch_attempt_id=tags.get("bioaf-launch"),
                        )
                    )
        return found

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
