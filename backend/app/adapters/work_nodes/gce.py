"""GCE work node adapter (ADR-043).

Manages GCE VM instances for SSH-accessible work nodes.
Supports local/mock mode for development and real GCE API for production.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from app.adapters.base import WorkNodeProvider

logger = logging.getLogger("bioaf.adapters.work_nodes.gce")

# In-memory session store for local mode
_local_vms: dict[str, dict] = {}


def _build_startup_script(vm_spec: dict) -> str:
    """Build the VM startup script from a work node spec.

    The Packer-built image already has conda, sshd, gcsfuse, and system
    packages installed. This script handles user-specific configuration.
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

    # 4. Copy input files from GCS
    if input_files:
        lines += [
            "",
            "# 4. Copy input files from GCS",
            "mkdir -p /data",
        ]
        for input_file in input_files:
            rel_path = input_file["relative_path"]
            gcs_uri = input_file["gcs_uri"]
            dest_path = f"/data/{rel_path}"
            dest_dir = "/".join(dest_path.split("/")[:-1])
            lines.append(
                f"mkdir -p '{dest_dir}' && "
                f"gsutil cp '{gcs_uri}' '{dest_path}' || "
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
            "# Allow bioaf-sync to read /outputs/ and run gsutil",
            "usermod -aG root bioaf-sync 2>/dev/null || true",
        ]

    # 7. Install shutdown sync service (fallback for unclean stops)
    if working_bucket and session_id:
        gcs_output_prefix = f"gs://{working_bucket}/sessions/{session_id}/outputs/"
        gcs_scripts_prefix = f"gs://{working_bucket}/sessions/{session_id}/scripts/"
        lines += [
            "",
            "# 6. Install shutdown sync service",
            "cat > /usr/local/bin/bioaf-shutdown-sync.sh << 'SYNCEOF'",
            "#!/bin/bash",
            'if [ -d /outputs ] && [ "$(ls -A /outputs)" ]; then',
            f"  gsutil -m rsync -r /outputs {gcs_output_prefix}",
            "fi",
            f"find /home -maxdepth 4 "
            r"\( -name '*.ipynb' -o -name '*.Rmd' -o -name '*.R' -o -name '*.py' \) "
            f"-type f "
            f'| while read f; do gsutil cp "$f" '
            f'{gcs_scripts_prefix}"$(basename "$f")"; done',
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

    # 7. Activate conda env in user's shell
    if env_name and env_name != "base":
        lines += [
            "",
            "# 6. Activate conda environment",
            f"echo 'source /opt/conda/etc/profile.d/conda.sh && conda activate {env_name}' >> {home_dir}/.bashrc",
        ]

    # 7. Generate MOTD
    repo_names = ", ".join(r["display_name"] for r in github_repos) if github_repos else "(none)"
    file_count = len(input_files)
    lines += [
        "",
        "# 7. Generate MOTD",
        "cat > /etc/motd << 'MOTD_EOF'",
        "",
        "=== bioAF Work Node ===",
        "",
        "  Input data:     /data/                    (copied from GCS at boot)",
        f"  Your repos:     {home_dir}/repos/          (cloned from GitHub)",
        "  Output files:   /outputs/                  (synced to GCS on stop)",
        "  Scratch space:  /scratch/                  (LOST on stop)",
        "",
        f"  Environment:    {env_label}",
        f"  Repos:          {repo_names}",
        f"  Input files:    {file_count} file(s)",
        "",
        "MOTD_EOF",
    ]

    # 8. Heartbeat agent
    if heartbeat_token:
        lines += [
            "",
            "# 8. Heartbeat agent",
            "mkdir -p /etc/bioaf",
            f"echo '{heartbeat_token}' > /etc/bioaf/token",
        ]
        # Simple heartbeat cron: POST every 5 minutes
        api_base = vm_spec.get("api_base_url", "")
        if api_base:
            lines += [
                f"echo '*/5 * * * * curl -s -X POST {api_base}/api/v1/work-nodes/sessions/{session_id}/heartbeat "
                f'-H "X-Heartbeat-Token: {heartbeat_token}" > /dev/null 2>&1\' | crontab -',
            ]

    # 9. Ownership
    lines += [
        "",
        "# 9. Final ownership",
        f"chown -R {username}:{username} {home_dir}",
    ]

    return "\n".join(lines)


class GCEWorkNodeProvider(WorkNodeProvider):
    """GCE work node backend with local mode for development."""

    def __init__(self, session_factory=None):
        self._mode = os.environ.get("BIOAF_COMPUTE_MODE", "local")
        self._session_factory = session_factory
        self._gcp_config: dict | None = None
        # In-memory store for sync SSH private keys, keyed by session_id.
        # Generated at launch, used at terminate to SSH in for output sync.
        self._sync_keys: dict[int, str] = {}

    @property
    def is_local(self) -> bool:
        return self._mode == "local"

    async def load_gcp_config(self, force: bool = False) -> dict:
        """Read GCP config from platform_config. Caches the result."""
        if self._gcp_config is not None and not force:
            return self._gcp_config

        from sqlalchemy import text as sa_text

        if not self._session_factory:
            self._gcp_config = {}
            return self._gcp_config

        async with self._session_factory() as session:
            result = await session.execute(
                sa_text(
                    "SELECT key, value FROM platform_config "
                    "WHERE key IN ("
                    "  'gcp_project_id', 'gcp_zone', 'gcp_region',"
                    "  'gcp_service_account_key', 'gcp_service_account_email',"
                    "  'notebook_runner_sa_email', 'working_bucket_name'"
                    ")"
                )
            )
            self._gcp_config = {r[0]: r[1] for r in result.fetchall()}

        return self._gcp_config

    def _get_gcp_credentials(self):
        """Build GCP credentials from service account key."""
        import json as _json

        from google.oauth2 import service_account

        cfg = self._gcp_config or {}
        sa_key = cfg.get("gcp_service_account_key", "")
        if not sa_key:
            raise RuntimeError("No GCP service account key in platform_config")

        key_data = _json.loads(sa_key)
        return service_account.Credentials.from_service_account_info(
            key_data,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

    async def launch_vm(self, vm_spec: dict) -> dict:
        if self.is_local:
            return self._local_launch_vm(vm_spec)
        return await self._gce_launch_vm(vm_spec)

    async def terminate_vm(self, instance_name: str, zone: str, **kwargs) -> dict:
        if self.is_local:
            return self._local_terminate_vm(instance_name)
        return await self._gce_terminate_vm(instance_name, zone, **kwargs)

    async def get_vm_status(self, instance_name: str, zone: str) -> dict:
        if self.is_local:
            return self._local_get_vm_status(instance_name)
        return await self._gce_get_vm_status(instance_name, zone)

    async def list_vms(self, filters: dict | None = None) -> list[dict]:
        if self.is_local:
            return self._local_list_vms(filters)
        return await self._gce_list_vms(filters)

    # -- GCE API implementations --

    async def _gce_launch_vm(self, vm_spec: dict) -> dict:
        """Create a GCE VM instance for a work node."""
        from google.cloud import compute_v1

        await self.load_gcp_config()
        cfg = self._gcp_config or {}

        project = vm_spec.get("gcp_project_id") or cfg.get("gcp_project_id", "")
        configured_zone = vm_spec.get("gcp_zone") or cfg.get("gcp_zone", "")
        if not project or not configured_zone:
            raise RuntimeError("GCP project or zone not configured")

        # Derive region and build a list of zones to try, avoiding repeated
        # capacity failures in a single zone.
        region = configured_zone.rsplit("-", 1)[0]
        zone_suffixes = ["b", "c", "f", "a"]
        zones_to_try = [f"{region}-{s}" for s in zone_suffixes]

        session_id = vm_spec.get("session_id", 0)
        user_id = vm_spec.get("user_id", 0)
        machine_type = vm_spec.get("machine_type", "n2-standard-4")
        image_uri = vm_spec.get("image_uri", "")

        if not image_uri:
            raise ValueError("No image_uri provided for work node")

        instance_name = f"bioaf-worknode-{session_id}"

        # Resolve GCE machine type for GPU types
        gce_machine_type = vm_spec.get("gce_machine_type", machine_type)

        # Generate an SSH key pair for the bioaf-sync user so the backend
        # can SSH into the VM at terminate time to sync outputs.
        import asyncssh

        sync_key = asyncssh.generate_private_key("ssh-ed25519")
        sync_private_pem = sync_key.export_private_key().decode()
        sync_public_key = sync_key.export_public_key().decode().strip()
        self._sync_keys[session_id] = sync_private_pem
        vm_spec["sync_public_key"] = sync_public_key

        # Build startup script
        startup_script = _build_startup_script(vm_spec)

        # Service account
        sa_email = (
            vm_spec.get("service_account_email")
            or cfg.get("notebook_runner_sa_email", "")
            or cfg.get("gcp_service_account_email", "")
        )

        credentials = self._get_gcp_credentials()
        instances_client = compute_v1.InstancesClient(credentials=credentials)

        # Try creating the VM across zones until one succeeds
        accelerator_type = vm_spec.get("accelerator_type")
        accelerator_count = vm_spec.get("accelerator_count", 1)
        zone = zones_to_try[0]  # default for error reporting

        for try_zone in zones_to_try:
            try:
                instance = compute_v1.Instance()
                instance.name = instance_name
                instance.machine_type = f"zones/{try_zone}/machineTypes/{gce_machine_type}"

                disk = compute_v1.AttachedDisk()
                disk.auto_delete = True
                disk.boot = True
                init_params = compute_v1.AttachedDiskInitializeParams()
                init_params.source_image = image_uri
                init_params.disk_size_gb = 200
                init_params.disk_type = f"zones/{try_zone}/diskTypes/pd-ssd"
                disk.initialize_params = init_params
                instance.disks = [disk]

                network_interface = compute_v1.NetworkInterface()
                network_interface.name = "global/networks/default"
                access_config = compute_v1.AccessConfig()
                access_config.name = "External NAT"
                access_config.type_ = "ONE_TO_ONE_NAT"
                network_interface.access_configs = [access_config]
                instance.network_interfaces = [network_interface]

                if sa_email:
                    sa = compute_v1.ServiceAccount()
                    sa.email = sa_email
                    sa.scopes = ["https://www.googleapis.com/auth/cloud-platform"]
                    instance.service_accounts = [sa]

                tags = compute_v1.Tags()
                tags.items = ["bioaf-work-node"]
                instance.tags = tags

                instance.labels = {
                    "bioaf-session": str(session_id),
                    "bioaf-user": str(user_id),
                    "bioaf-managed": "true",
                }

                metadata = compute_v1.Metadata()
                metadata.items = [
                    compute_v1.Items(key="startup-script", value=startup_script),
                ]
                instance.metadata = metadata

                if accelerator_type:
                    accel = compute_v1.AcceleratorConfig()
                    accel.accelerator_type = f"zones/{try_zone}/acceleratorTypes/{accelerator_type}"
                    accel.accelerator_count = accelerator_count
                    instance.guest_accelerators = [accel]
                    scheduling = compute_v1.Scheduling()
                    scheduling.on_host_maintenance = "TERMINATE"
                    instance.scheduling = scheduling

                instances_client.insert(
                    project=project,
                    zone=try_zone,
                    instance_resource=instance,
                )
                zone = try_zone
                logger.info("Creating GCE instance %s in %s/%s", instance_name, project, zone)
                break
            except Exception as e:
                if "ZONE_RESOURCE_POOL_EXHAUSTED" in str(e) or "does not have enough resources" in str(e):
                    logger.warning("Zone %s exhausted for %s, trying next zone", try_zone, machine_type)
                    continue
                raise
        else:
            # All zones exhausted -- for loop completed without break
            raise ValueError(
                f"GCP resources unavailable: no {machine_type} capacity in any {region} zone. "
                "Try again later or choose a different machine type."
            )

        # Launch background poller
        creds = vm_spec.get("session_credentials", {})
        ssh_username = creds.get("username", "")
        asyncio.create_task(self._poll_vm_ready(session_id, instance_name, project, zone, ssh_username))

        return {
            "instance_name": instance_name,
            "zone": zone,
            "gcp_project_id": project,
            "status": "starting",
            "access_url": None,
        }

    async def _poll_vm_ready(
        self,
        session_id: int,
        instance_name: str,
        project: str,
        zone: str,
        ssh_username: str = "",
    ) -> None:
        """Background: poll for VM running status + external IP, then update DB."""
        try:
            from google.cloud import compute_v1

            credentials = self._get_gcp_credentials()
            instances_client = compute_v1.InstancesClient(credentials=credentials)

            external_ip = None
            for _ in range(60):  # up to 5 minutes
                try:
                    instance = instances_client.get(project=project, zone=zone, instance=instance_name)
                    if instance.status == "RUNNING":
                        for iface in instance.network_interfaces:
                            for ac in iface.access_configs:
                                if ac.nat_i_p:
                                    external_ip = ac.nat_i_p
                                    break
                            if external_ip:
                                break
                        if external_ip:
                            break
                    elif instance.status in ("TERMINATED", "STOPPED", "SUSPENDED"):
                        logger.error("VM %s entered %s status", instance_name, instance.status)
                        await self._update_session_in_db(session_id, status="failed", access_url=None)
                        return
                except Exception:
                    pass
                await asyncio.sleep(5)

            if not external_ip:
                logger.error("VM %s not running with external IP after 5 min", instance_name)
                await self._update_session_in_db(session_id, status="failed", access_url=None)
                return

            user_prefix = f"{ssh_username}@" if ssh_username else ""
            access_url = f"ssh://{user_prefix}{external_ip}:22"
            logger.info("VM %s ready at %s", instance_name, external_ip)
            await self._update_session_in_db(session_id, status="running", access_url=access_url)

        except Exception:
            logger.exception("Background poll failed for VM session %s", session_id)
            await self._update_session_in_db(session_id, status="failed", access_url=None)

    async def _update_session_in_db(self, session_id: int, status: str, access_url: str | None) -> None:
        """Update a work node session's status and access_url in the DB."""
        if not self._session_factory:
            logger.warning("No session_factory, cannot update session %s", session_id)
            return

        try:
            async with self._session_factory() as db:
                from sqlalchemy import text

                await db.execute(
                    text("UPDATE compute_sessions SET status = :status, access_url = :url WHERE id = :id"),
                    {"status": status, "url": access_url, "id": session_id},
                )
                await db.commit()
                logger.info("Updated session %s: status=%s access_url=%s", session_id, status, access_url)
        except Exception:
            logger.exception("Failed to update session %s in DB", session_id)

    async def _gce_terminate_vm(
        self,
        instance_name: str,
        zone: str,
        *,
        gcp_project_id: str = "",
        session_id: int = 0,
        working_bucket: str = "",
        **kwargs,
    ) -> dict:
        """Sync outputs from VM, then delete it."""
        from google.cloud import compute_v1

        await self.load_gcp_config()
        cfg = self._gcp_config or {}
        project = gcp_project_id or cfg.get("gcp_project_id", "")

        output_files: list[dict] = []
        gcs_output_prefix = ""

        # Sync outputs from the running VM before stopping it.
        # SSH into the VM as the bioaf-sync user (key-based) and run gsutil,
        # mirroring the K8s approach of exec'ing into the pod before deletion.
        if working_bucket and session_id:
            gcs_output_prefix = f"gs://{working_bucket}/sessions/{session_id}/outputs/"
            gcs_scripts_prefix = f"gs://{working_bucket}/sessions/{session_id}/scripts/"

            # Get the VM's external IP
            external_ip = None
            try:
                credentials = self._get_gcp_credentials()
                instances_client = compute_v1.InstancesClient(credentials=credentials)
                instance = instances_client.get(project=project, zone=zone, instance=instance_name)
                for iface in instance.network_interfaces:
                    for ac in iface.access_configs:
                        if ac.nat_i_p:
                            external_ip = ac.nat_i_p
                            break
            except Exception as e:
                logger.warning("Could not get external IP for VM %s: %s", instance_name, e)

            if external_ip:
                sync_cmd = (
                    f'if [ -d /outputs ] && [ "$(ls -A /outputs)" ]; then '
                    f"gsutil -m rsync -r /outputs {gcs_output_prefix}; fi; "
                    f"find /home -maxdepth 4 "
                    r"\( -name '*.ipynb' -o -name '*.Rmd' -o -name '*.R' -o -name '*.py' \) "
                    f"-type f "
                    f'| while read f; do gsutil cp "$f" '
                    f'{gcs_scripts_prefix}"$(basename "$f")"; done'
                )
                try:
                    import asyncssh

                    # Read the sync private key that was embedded at launch
                    sync_key = self._sync_keys.get(session_id)
                    if sync_key:
                        key = asyncssh.import_private_key(sync_key)
                        async with asyncssh.connect(
                            external_ip,
                            port=22,
                            username="bioaf-sync",
                            client_keys=[key],
                            known_hosts=None,
                        ) as conn:
                            result = await asyncio.wait_for(conn.run(sync_cmd), timeout=300)
                            if result.exit_status == 0:
                                logger.info("Output sync complete for VM %s", instance_name)
                            else:
                                logger.warning(
                                    "Output sync returned %d for VM %s: %s",
                                    result.exit_status,
                                    instance_name,
                                    result.stderr,
                                )
                    else:
                        logger.warning("No sync key for session %d, skipping output sync", session_id)
                except Exception as e:
                    logger.warning("SSH output sync failed for VM %s: %s", instance_name, e)

        # Stop and delete the VM
        try:
            credentials = self._get_gcp_credentials()
            instances_client = compute_v1.InstancesClient(credentials=credentials)
            instances_client.stop(project=project, zone=zone, instance=instance_name)

            for _ in range(60):
                try:
                    instance = instances_client.get(project=project, zone=zone, instance=instance_name)
                    if instance.status in ("TERMINATED", "STOPPED"):
                        break
                except Exception:
                    break
                await asyncio.sleep(5)

            logger.info("VM %s stopped", instance_name)
        except Exception as e:
            logger.warning("Failed to stop VM %s: %s", instance_name, e)

        # Delete the VM
        try:
            credentials = self._get_gcp_credentials()
            instances_client = compute_v1.InstancesClient(credentials=credentials)
            instances_client.delete(project=project, zone=zone, instance=instance_name)
            logger.info("Deleted VM %s", instance_name)
        except Exception as e:
            logger.warning("Failed to delete VM %s: %s", instance_name, e)

        # Clean up sync key
        self._sync_keys.pop(session_id, None)

        # List output files from GCS
        if gcs_output_prefix:
            try:
                from google.cloud import storage

                credentials = self._get_gcp_credentials()
                storage_client = storage.Client(project=project, credentials=credentials)
                bucket = storage_client.bucket(working_bucket)
                prefix = f"sessions/{session_id}/"
                blobs = bucket.list_blobs(prefix=prefix)
                for blob in blobs:
                    output_files.append(
                        {
                            "gcs_uri": f"gs://{working_bucket}/{blob.name}",
                            "size_bytes": blob.size or 0,
                            "filename": blob.name.split("/")[-1],
                        }
                    )
            except Exception as e:
                logger.warning("Failed to list output files for session %s: %s", session_id, e)

        return {
            "instance_name": instance_name,
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "output_files": output_files,
            "gcs_output_prefix": gcs_output_prefix,
        }

    async def _gce_get_vm_status(self, instance_name: str, zone: str) -> dict:
        """Query GCE API for VM status."""
        from google.cloud import compute_v1

        await self.load_gcp_config()
        cfg = self._gcp_config or {}
        project = cfg.get("gcp_project_id", "")

        try:
            credentials = self._get_gcp_credentials()
            instances_client = compute_v1.InstancesClient(credentials=credentials)
            instance = instances_client.get(project=project, zone=zone, instance=instance_name)

            external_ip = None
            for iface in instance.network_interfaces:
                for ac in iface.access_configs:
                    if ac.nat_i_p:
                        external_ip = ac.nat_i_p
                        break

            status_map = {
                "RUNNING": "running",
                "STAGING": "starting",
                "PROVISIONING": "starting",
                "STOPPING": "stopping",
                "TERMINATED": "stopped",
                "STOPPED": "stopped",
                "SUSPENDED": "stopped",
            }

            return {
                "instance_name": instance_name,
                "status": status_map.get(instance.status, "unknown"),
                "external_ip": external_ip,
                "zone": zone,
            }
        except Exception:
            return {
                "instance_name": instance_name,
                "status": "unknown",
                "zone": zone,
            }

    async def _gce_list_vms(self, filters: dict | None = None) -> list[dict]:
        """List work node VMs by label."""
        from google.cloud import compute_v1

        await self.load_gcp_config()
        cfg = self._gcp_config or {}
        project = cfg.get("gcp_project_id", "")
        zone = cfg.get("gcp_zone", "")

        try:
            credentials = self._get_gcp_credentials()
            instances_client = compute_v1.InstancesClient(credentials=credentials)

            request = compute_v1.ListInstancesRequest(
                project=project,
                zone=zone,
                filter='labels.bioaf-managed="true"',
            )
            instances = instances_client.list(request=request)

            vms = []
            for instance in instances:
                labels = dict(instance.labels) if instance.labels else {}
                external_ip = None
                for iface in instance.network_interfaces:
                    for ac in iface.access_configs:
                        if ac.nat_i_p:
                            external_ip = ac.nat_i_p
                            break

                vms.append(
                    {
                        "instance_name": instance.name,
                        "status": instance.status.lower(),
                        "external_ip": external_ip,
                        "zone": zone,
                        "session_id": labels.get("bioaf-session", ""),
                        "user_id": labels.get("bioaf-user", ""),
                    }
                )
            return vms
        except Exception:
            logger.exception("Failed to list GCE VMs")
            return []

    # -- Local mode implementations --

    def _local_launch_vm(self, vm_spec: dict) -> dict:
        instance_name = f"bioaf-worknode-local-{uuid.uuid4().hex[:8]}"
        vm_data = {
            "instance_name": instance_name,
            "status": "running",
            "access_url": "ssh://127.0.0.1:22",
            "zone": "us-central1-a",
            "gcp_project_id": "local-project",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _local_vms[instance_name] = vm_data
        logger.info("Local mode: launched VM %s", instance_name)
        return vm_data

    def _local_terminate_vm(self, instance_name: str) -> dict:
        if instance_name in _local_vms:
            _local_vms[instance_name]["status"] = "stopped"
            _local_vms[instance_name]["stopped_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("Local mode: terminated VM %s", instance_name)
        return {
            "instance_name": instance_name,
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "output_files": [],
            "gcs_output_prefix": "",
        }

    def _local_get_vm_status(self, instance_name: str) -> dict:
        if instance_name in _local_vms:
            return _local_vms[instance_name]
        return {"instance_name": instance_name, "status": "unknown"}

    def _local_list_vms(self, filters: dict | None = None) -> list[dict]:
        return list(_local_vms.values())
