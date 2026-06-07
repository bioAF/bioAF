"""Kubernetes notebook adapter.

Supports local/mock mode for development and real K8s API for production.
Mode is controlled by the BIOAF_COMPUTE_MODE environment variable.

When running outside the cluster (e.g., Docker Compose on a VM), the adapter
builds a K8s client from platform_config credentials (gke_cluster_endpoint,
gke_cluster_ca_cert) and a GCP access token from the credential injector
(impersonated bootstrap on vm_default installs, JSON key on legacy installs).
"""

import asyncio
import base64
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone

from kubernetes import client, config

from app.adapters.base import NotebookProvider
from app.adapters.capabilities import ProviderCapabilities
from app.adapters.models import (
    SessionInfo,
    SessionStatus,
    StoredObject,
    TerminationResult,
    to_service_state,
)
from app.services.session_persistence import (
    generate_sync_in_command,
    generate_sync_out_command,
)

logger = logging.getLogger("bioaf.adapters.notebooks.k8s")

_SESSION_INFO_KEYS = {"session_id", "status", "access_url", "session_type", "created_at"}
_SESSION_STATUS_KEYS = {"session_id", "status", "access_url", "session_type", "user_id"}


def _session_info_from_dict(d: dict) -> SessionInfo:
    return SessionInfo(
        session_id=str(d.get("session_id", "")),
        status=to_service_state(d.get("status")),
        access_url=d.get("access_url"),
        session_type=d.get("session_type"),
        created_at=d.get("created_at"),
        provider_details={k: v for k, v in d.items() if k not in _SESSION_INFO_KEYS},
    )


def _session_status_from_dict(d: dict) -> SessionStatus:
    return SessionStatus(
        session_id=str(d.get("session_id", "")),
        status=to_service_state(d.get("status")),
        access_url=d.get("access_url"),
        session_type=d.get("session_type"),
        user_id=d.get("user_id"),
        provider_details={k: v for k, v in d.items() if k not in _SESSION_STATUS_KEYS},
    )


def _session_termination_from_dict(d: dict) -> TerminationResult:
    return TerminationResult(
        status=to_service_state(d.get("status")),
        output_files=[
            StoredObject(filename=o["filename"], storage_uri=o.get("gcs_uri", ""), size_bytes=o.get("size_bytes"))
            for o in d.get("output_files", [])
        ],
        output_prefix=d.get("gcs_output_prefix", ""),
        provider_details={"session_id": d.get("session_id"), "stopped_at": d.get("stopped_at")},
    )


def _get_gcp_token(gcp_config: dict) -> str:
    """Mint a GCP access token via credential_injector.

    Returns a Bearer token suitable for the K8s API. In vm_default mode this
    uses bioaf-app's metadata identity, optionally impersonating bioaf-bootstrap
    when configured. In legacy service_account_key mode it uses the stored key.
    """
    import google.auth.transport.requests

    from app.platform import credential_injector

    credentials = credential_injector.load_gcp_credentials(gcp_config)
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


# In-memory session store for local mode
_local_sessions: dict[str, dict] = {}

DEFAULT_NOTEBOOK_NAMESPACE = "bioaf-notebooks"
HOME_DIR = "/home/jovyan"


class KubernetesNotebookProvider(NotebookProvider):
    """Kubernetes notebook backend with local mode for development."""

    # GCP access tokens expire after 3600s; rebuild client before that
    _TOKEN_TTL_SECONDS = 2700  # 45 minutes

    def __init__(self, session_factory=None):
        self._mode = os.environ.get("BIOAF_COMPUTE_MODE", "local")
        self._session_factory = session_factory
        self._api_client: client.ApiClient | None = None
        self._client_created_at: float = 0.0
        # Fingerprint of the cluster the cached _api_client was built for.
        # Used to invalidate the cache when platform_config now points at a
        # different cluster (e.g. after a teardown + redeploy).
        self._cached_cluster_fingerprint: tuple[str, str] = ("", "")
        self._cluster_config: dict | None = None
        self._namespace_ready = False

    def capabilities(self) -> ProviderCapabilities:
        """Kubernetes notebook backend supports interactive notebook sessions."""
        return ProviderCapabilities(notebooks=True)

    def _cluster_fingerprint(self) -> tuple[str, str]:
        """(endpoint, ca_cert) identity of the cluster in the current config."""
        cfg = self._cluster_config or {}
        return (
            cfg.get("gke_cluster_endpoint", "") or "",
            cfg.get("gke_cluster_ca_cert", "") or "",
        )

    @property
    def is_local(self) -> bool:
        return self._mode == "local"

    async def launch_session(self, session_spec: dict) -> SessionInfo:
        d = self._local_launch_session(session_spec) if self.is_local else await self._k8s_launch_session(
            session_spec
        )
        return _session_info_from_dict(d)

    async def terminate_session(self, session_id: str, **kwargs) -> TerminationResult:
        d = (
            self._local_terminate_session(session_id)
            if self.is_local
            else await self._k8s_terminate_session(session_id=session_id, **kwargs)
        )
        return _session_termination_from_dict(d)

    async def get_session_status(self, session_id: str, **kwargs) -> SessionStatus:
        d = (
            self._local_get_session_status(session_id)
            if self.is_local
            else await self._k8s_get_session_status(session_id=session_id, **kwargs)
        )
        return _session_status_from_dict(d)

    async def list_sessions(self, filters: dict | None = None) -> list[SessionStatus]:
        items = self._local_list_sessions(filters) if self.is_local else await self._k8s_list_sessions(filters)
        return [_session_status_from_dict(d) for d in items]

    async def get_connection_command(self, session_id: str) -> str:
        namespace = DEFAULT_NOTEBOOK_NAMESPACE
        return f"kubectl exec -it -n {namespace} pod/bioaf-notebook-{session_id} -- /bin/bash"

    # -- K8s client helpers --

    async def load_cluster_config(self, force: bool = False) -> dict:
        """Read GKE cluster config from platform_config.

        Caches the result. Re-reads when forced or when the cached endpoint
        is missing/null so newly deployed clusters are picked up.
        """
        if self._cluster_config is not None and not force:
            endpoint = self._cluster_config.get("gke_cluster_endpoint", "")
            if endpoint and endpoint != "null":
                return self._cluster_config

        if not self._session_factory:
            self._cluster_config = {}
            return self._cluster_config

        async with self._session_factory() as session:
            from app.platform.platform_config_service import PlatformConfigService

            self._cluster_config = await PlatformConfigService.get_many(
                session,
                [
                    "gke_cluster_endpoint",
                    "gke_cluster_ca_cert",
                    "gcp_credential_source",
                    "gcp_service_account_key",
                    "gcp_service_account_email",
                    "gcp_bootstrap_sa_email",
                    "gke_cluster_name",
                    "gcp_project_id",
                    "gcp_zone",
                ],
            )

        return self._cluster_config

    def _build_out_of_cluster_client(self) -> client.ApiClient:
        """Build a K8s ApiClient using platform_config credentials.

        Requires load_cluster_config() to have been called first.
        """
        cfg = self._cluster_config or {}

        endpoint = cfg.get("gke_cluster_endpoint", "")
        ca_cert_b64 = cfg.get("gke_cluster_ca_cert", "")

        if not endpoint or endpoint == "null":
            raise RuntimeError("No GKE cluster endpoint in platform_config. Deploy the compute stack first.")

        if not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}"

        token = _get_gcp_token(cfg)

        ca_cert_bytes = base64.b64decode(ca_cert_b64)
        ca_file = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
        ca_file.write(ca_cert_bytes)
        ca_file.close()

        configuration = client.Configuration()
        configuration.host = endpoint
        configuration.ssl_ca_cert = ca_file.name

        api_client = client.ApiClient(configuration)
        # The kubernetes-python client does not route Configuration.api_key
        # into request headers unless an OpenAPI security scheme references
        # it. The K8s OpenAPI spec the library ships with does not declare
        # such a scheme for the simple bearer-token case, so api_key here is
        # a silent no-op and every request goes out anonymously, yielding
        # 401 Unauthorized. Set the header on the client directly instead.
        api_client.set_default_header("Authorization", f"Bearer {token}")

        self._client_created_at = time.monotonic()
        return api_client

    def _is_token_expired(self) -> bool:
        """Check if the cached GCP access token is older than the TTL."""
        if self._client_created_at == 0.0:
            return False
        return (time.monotonic() - self._client_created_at) > self._TOKEN_TTL_SECONDS

    async def _get_api_client_async(self) -> client.ApiClient:
        """Get or create a K8s ApiClient, trying incluster first.

        Re-reads platform_config on every call so that a cluster teardown +
        redeploy (which rewrites gke_cluster_endpoint and gke_cluster_ca_cert)
        invalidates the cached client. Without this check the backend keeps
        the previous cluster's endpoint, CA, and bearer token in memory until
        the GCP access token TTL elapses, and every notebook launch in that
        window fails with 401 Unauthorized.

        Falls back to platform_config credentials when not running in a pod.
        """
        await self.load_cluster_config(force=True)
        current_fp = self._cluster_fingerprint()

        cluster_changed = self._api_client is not None and self._cached_cluster_fingerprint != current_fp
        if self._api_client is not None and not self._is_token_expired() and not cluster_changed:
            return self._api_client

        if self._is_token_expired():
            logger.info("GCP access token approaching expiry, refreshing K8s client")
        elif cluster_changed:
            logger.info(
                "Cluster identity changed in platform_config, rebuilding K8s client (old endpoint=%s, new endpoint=%s)",
                self._cached_cluster_fingerprint[0],
                current_fp[0],
            )
        self._api_client = None

        try:
            config.load_incluster_config()
            self._api_client = client.ApiClient()
            logger.info("Using incluster K8s config")
        except Exception:
            logger.info("Not running in cluster, using platform_config credentials")
            try:
                self._api_client = self._build_out_of_cluster_client()
                self._cached_cluster_fingerprint = current_fp
                logger.info("K8s client built for endpoint %s", current_fp[0])
            except Exception:
                logger.exception("Failed to build out-of-cluster K8s client")
                raise

        return self._api_client

    def _get_api_client(self) -> client.ApiClient:
        """Get or create a K8s ApiClient (sync version).

        Uses cached client if available; does not reload from DB.
        """
        if self._api_client is not None and not self._is_token_expired():
            return self._api_client

        if self._is_token_expired():
            logger.info("GCP access token approaching expiry, refreshing K8s client")
            self._api_client = None

        try:
            config.load_incluster_config()
            self._api_client = client.ApiClient()
            logger.info("Using incluster K8s config")
        except Exception:
            logger.info("Not running in cluster, using platform_config credentials")
            try:
                self._api_client = self._build_out_of_cluster_client()
                logger.info(
                    "K8s client built for endpoint %s",
                    (self._cluster_config or {}).get("gke_cluster_endpoint"),
                )
            except Exception:
                logger.exception("Failed to build out-of-cluster K8s client")
                raise

        return self._api_client

    def _get_k8s_core_client(self):
        """Get a Kubernetes CoreV1Api client. Tests mock this method."""
        return client.CoreV1Api(api_client=self._get_api_client())

    def _get_k8s_rbac_client(self):
        """Get a Kubernetes RbacAuthorizationV1Api client. Tests mock this method."""
        return client.RbacAuthorizationV1Api(api_client=self._get_api_client())

    # -- Namespace setup --

    async def ensure_notebook_namespace(
        self, namespace: str = DEFAULT_NOTEBOOK_NAMESPACE, gcp_sa_email: str = ""
    ) -> None:
        """Ensure the notebook namespace and service account exist."""
        from kubernetes.client.rest import ApiException

        # Always patch the SA annotation when a SA email is provided, even if
        # the namespace was already set up on a previous call.  The annotation
        # may be missing if the namespace was created before Workload Identity
        # was configured.
        if self._namespace_ready:
            if gcp_sa_email:
                core_v1 = self._get_k8s_core_client()
                self._patch_sa_annotation(core_v1, namespace, gcp_sa_email)
            return

        core_v1 = self._get_k8s_core_client()
        rbac_v1 = self._get_k8s_rbac_client()

        try:
            core_v1.read_namespace(name=namespace)
            logger.info("Namespace %s already exists, skipping setup", namespace)
            # Patch the SA annotation in case it was created before WI was configured
            if gcp_sa_email:
                self._patch_sa_annotation(core_v1, namespace, gcp_sa_email)
            self._namespace_ready = True
            return
        except ApiException as e:
            if e.status != 404:
                raise

        core_v1.create_namespace(
            body=client.V1Namespace(
                metadata=client.V1ObjectMeta(
                    name=namespace,
                    labels={"bioaf.io/managed": "true"},
                )
            )
        )
        logger.info("Created namespace %s", namespace)

        # Build SA annotations for Workload Identity
        sa_annotations = {}
        if gcp_sa_email:
            sa_annotations["iam.gke.io/gcp-service-account"] = gcp_sa_email

        core_v1.create_namespaced_service_account(
            namespace=namespace,
            body=client.V1ServiceAccount(
                metadata=client.V1ObjectMeta(
                    name="bioaf-notebook-runner",
                    labels={"bioaf.io/managed": "true"},
                    annotations=sa_annotations or None,
                )
            ),
        )
        logger.info("Created service account bioaf-notebook-runner in %s", namespace)

        rbac_v1.create_namespaced_role_binding(
            namespace=namespace,
            body=client.V1RoleBinding(
                metadata=client.V1ObjectMeta(
                    name="bioaf-notebook-runner-binding",
                    labels={"bioaf.io/managed": "true"},
                ),
                role_ref=client.V1RoleRef(
                    api_group="rbac.authorization.k8s.io",
                    kind="ClusterRole",
                    name="edit",
                ),
                subjects=[
                    client.RbacV1Subject(
                        kind="ServiceAccount",
                        name="bioaf-notebook-runner",
                        namespace=namespace,
                    )
                ],
            ),
        )
        logger.info("Created role binding in %s", namespace)
        self._namespace_ready = True

    def _ensure_gcs_secret(self, namespace: str) -> bool:
        """Create a K8s Secret with the GCP SA key for GCS access.

        Returns True if the secret exists (created or already present).
        """
        import base64 as _b64

        from kubernetes.client.rest import ApiException

        cfg = self._cluster_config or {}
        sa_key = cfg.get("gcp_service_account_key", "")
        if not sa_key:
            return False

        core_client = self._get_k8s_core_client()
        secret_name = "bioaf-gcs-sa-key"

        try:
            core_client.read_namespaced_secret(name=secret_name, namespace=namespace)
            return True
        except ApiException as e:
            if e.status != 404:
                logger.warning("Error checking GCS secret: %s", e)
                return False

        core_client.create_namespaced_secret(
            namespace=namespace,
            body={
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": secret_name, "labels": {"bioaf.io/managed": "true"}},
                "type": "Opaque",
                "data": {"key.json": _b64.b64encode(sa_key.encode()).decode()},
            },
        )
        logger.info("Created GCS SA key secret in %s", namespace)
        return True

    @staticmethod
    def _patch_sa_annotation(core_v1, namespace: str, gcp_sa_email: str) -> None:
        """Ensure the notebook-runner SA has the Workload Identity annotation."""
        try:
            sa = core_v1.read_namespaced_service_account(name="bioaf-notebook-runner", namespace=namespace)
            current = (sa.metadata.annotations or {}).get("iam.gke.io/gcp-service-account", "")
            if current != gcp_sa_email:
                core_v1.patch_namespaced_service_account(
                    name="bioaf-notebook-runner",
                    namespace=namespace,
                    body={"metadata": {"annotations": {"iam.gke.io/gcp-service-account": gcp_sa_email}}},
                )
                logger.info("Patched Workload Identity annotation on bioaf-notebook-runner")
        except Exception:
            logger.exception("Failed to patch Workload Identity annotation on bioaf-notebook-runner")

    # -- K8s API implementations (production) --

    def _build_pod_manifest(self, session_spec: dict, has_gcs_secret: bool = False) -> dict:
        """Build a Kubernetes Pod manifest from a session spec.

        Extracted from _k8s_launch_session so it can be unit-tested without
        requiring a live K8s API client.
        """
        session_id = session_spec.get("session_id", 0)
        session_type = session_spec.get("session_type", "jupyter")
        user_id = session_spec.get("user_id", 0)
        namespace = DEFAULT_NOTEBOOK_NAMESPACE

        pod_name = f"bioaf-notebook-{session_id}"
        working_bucket = session_spec.get("working_bucket", "bioaf-working")
        gcs_home_prefix = f"gs://{working_bucket}/notebooks/{user_id}/"

        # Determine container command based on session type
        if session_type == "jupyter":
            container_port = 8888
            container_command = [
                "jupyter",
                "lab",
                "--ip=0.0.0.0",
                f"--port={container_port}",
                "--no-browser",
                "--NotebookApp.token=''",
                "--NotebookApp.password=''",
            ]
        elif session_type == "ssh":
            # SSH work node (ADR-034): sshd as main process with PAM auth
            session_creds = session_spec.get("session_credentials")
            if not session_creds:
                raise ValueError("Session credentials are required for SSH work nodes")

            container_port = 22
            cred_username = session_creds["username"]
            cred_password = session_creds.get("password_hash") or session_creds.get("password", "")
            home_dir = f"/home/{cred_username}"

            if cred_password.startswith("$2"):
                chpasswd_cmd = f"echo '{cred_username}:{cred_password}' | chpasswd -e"
            else:
                chpasswd_cmd = f"echo '{cred_username}:{cred_password}' | chpasswd"

            # Write heartbeat token to /etc/bioaf/token for the bioaf CLI
            heartbeat_token = session_spec.get("heartbeat_token", "")
            startup_script = (
                f"useradd -m -d {home_dir} -s /bin/bash {cred_username} || true && "
                f"{chpasswd_cmd} && "
                f"chown -R {cred_username}:{cred_username} {home_dir} && "
                f"mkdir -p /etc/bioaf && echo '{heartbeat_token}' > /etc/bioaf/token && "
                "mkdir -p /run/sshd && "
                "exec /usr/sbin/sshd -D"
            )
            container_command = ["/bin/sh", "-c", startup_script]
        else:
            # RStudio uses PAM auth -- session credentials are required.
            # User creation must happen inside the main container (not an
            # init container) because /etc/passwd and /etc/shadow are part
            # of each container's own root filesystem and are not shared.
            session_creds = session_spec.get("session_credentials")
            if not session_creds:
                raise ValueError("Session credentials are required for RStudio sessions")

            container_port = 8787
            cred_username = session_creds["username"]
            cred_password = session_creds.get("password_hash") or session_creds.get("password", "")

            if cred_password.startswith("$2"):
                chpasswd_cmd = f"echo '{cred_username}:{cred_password}' | chpasswd -e"
            else:
                chpasswd_cmd = f"echo '{cred_username}:{cred_password}' | chpasswd"

            # SSH key setup for git
            ssh_setup = ""
            ssh_private_key = session_spec.get("ssh_private_key")
            if ssh_private_key:
                # Escape the key for shell embedding
                escaped_key = ssh_private_key.replace("'", "'\\''")
                ssh_setup = (
                    f"mkdir -p {HOME_DIR}/.ssh && "
                    f"printf '%s\\n' '{escaped_key}' > {HOME_DIR}/.ssh/id_rsa && "
                    f"chmod 600 {HOME_DIR}/.ssh/id_rsa && "
                    f"ssh-keyscan github.com >> {HOME_DIR}/.ssh/known_hosts 2>/dev/null && "
                    f"chown -R {cred_username}:{cred_username} {HOME_DIR}/.ssh && "
                )

            # Build git setup commands if git_config is provided
            git_setup = ""
            git_config = session_spec.get("git_config")
            if git_config:
                repo_url = git_config["repo_url"]
                branch = git_config["branch"]
                git_user_name = git_config.get("user_name", "bioaf")
                git_user_email = git_config.get("user_email", "bioaf@localhost")
                git_setup = (
                    f"git config --global user.name '{git_user_name}' && "
                    f"git config --global user.email '{git_user_email}' && "
                    f"git config --global init.defaultBranch main && "
                    f"cd {HOME_DIR} && "
                    f"git clone {repo_url} notebooks 2>/dev/null || "
                    f"(mkdir -p notebooks && cd notebooks && git init && "
                    f"git remote add origin {repo_url} && "
                    f"echo '# Notebook workspace' > README.md && "
                    f"git add -A && git commit -m 'Initial commit' && "
                    f"git push -u origin main 2>/dev/null) && "
                    f"cd {HOME_DIR}/notebooks && "
                    f"git checkout -b {branch} && "
                    f"chown -R {cred_username}:{cred_username} {HOME_DIR}/notebooks && "
                )

            startup_script = (
                f"useradd -m -d {HOME_DIR} -s /bin/bash {cred_username} || true && "
                f"{chpasswd_cmd} && "
                f"chown -R {cred_username}:{cred_username} {HOME_DIR} && "
                f"{ssh_setup}"
                f"{git_setup}"
                f"exec /usr/lib/rstudio-server/bin/rserver "
                f"--www-address=0.0.0.0 --www-port={container_port} --server-daemonize=0"
            )
            container_command = ["/bin/sh", "-c", startup_script]

        # Determine home directory and GCS prefix based on session type
        if session_type == "ssh":
            session_creds = session_spec.get("session_credentials", {})
            cred_username = session_creds.get("username", "bioaf")
            home_dir = f"/home/{cred_username}"
            gcs_home_prefix = f"gs://{working_bucket}/home/{user_id}/"
        else:
            home_dir = HOME_DIR

        # Build GCS sync init container
        sync_in_cmd = generate_sync_in_command(gcs_home_prefix, home_dir)
        init_container = {
            "name": "gcs-sync-in",
            "image": "google/cloud-sdk:slim",
            "command": sync_in_cmd,
            "volumeMounts": [{"name": "home", "mountPath": home_dir}],
        }

        init_containers = [init_container]

        # Build main container
        image = session_spec.get("image", "bioaf-scrna:latest")
        volume_mounts = [{"name": "home", "mountPath": home_dir}]
        volumes = [{"name": "home", "emptyDir": {"sizeLimit": "10Gi"}}]

        # Input file data sync init container
        input_files = session_spec.get("input_files", [])
        if input_files:
            # Create subdirectories and copy files preserving hierarchy
            copy_cmds: list[str] = []
            for f in input_files:
                dest_path = f"/data/{f['relative_path']}"
                dest_dir = "/".join(dest_path.split("/")[:-1])
                copy_cmds.append(f"mkdir -p {dest_dir} && gsutil cp {f['gcs_uri']} {dest_path}")
            # Generate FILE_INVENTORY.md using a heredoc to avoid backtick
            # interpretation by the shell (backticks in markdown trigger
            # command substitution inside double-quoted printf)
            inventory_lines = ["# File Inventory", "", "Files mounted at session start:", ""]
            for f in input_files:
                inventory_lines.append(f"- /data/{f['relative_path']} (source: {f['gcs_uri']})")
            inventory_content = "\n".join(inventory_lines)
            # Use heredoc with single-quoted delimiter to prevent all expansion
            copy_cmds.append(f"cat > /data/FILE_INVENTORY.md << 'INVENTORY_EOF'\n{inventory_content}\nINVENTORY_EOF")
            data_sync_cmd = " && ".join(copy_cmds)
            init_containers.append(
                {
                    "name": "gcs-data-sync",
                    "image": "google/cloud-sdk:slim",
                    "command": ["/bin/sh", "-c", data_sync_cmd],
                    "volumeMounts": [{"name": "data", "mountPath": "/data"}],
                }
            )
            volumes.append({"name": "data", "emptyDir": {"sizeLimit": "50Gi"}})
            volume_mounts.append({"name": "data", "mountPath": "/data", "readOnly": True})

        # Writable /outputs/ directory for all session types (ADR-040)
        volume_mounts.append({"name": "outputs", "mountPath": "/outputs"})
        volumes.append({"name": "outputs", "emptyDir": {"sizeLimit": "50Gi"}})

        # Track whether any GCS FUSE CSI volumes are used
        has_fuse_volumes = False

        # SSH work nodes get additional volumes: scratch and data mounts
        if session_type == "ssh":
            volume_mounts.append({"name": "scratch", "mountPath": "/scratch"})
            volumes.append({"name": "scratch", "emptyDir": {"sizeLimit": "100Gi"}})

            # GCS FUSE data mounts (read-only)
            data_mount_paths = session_spec.get("data_mount_paths", [])
            if data_mount_paths:
                has_fuse_volumes = True
            for i, mount_path in enumerate(data_mount_paths):
                vol_name = f"data-{i}"
                volume_mounts.append(
                    {
                        "name": vol_name,
                        "mountPath": f"/data/{mount_path.lstrip('/')}",
                        "readOnly": True,
                    }
                )
                volumes.append(
                    {
                        "name": vol_name,
                        "csi": {
                            "driver": "gcsfuse.csi.storage.gke.io",
                            "readOnly": True,
                            "volumeAttributes": {
                                "bucketName": working_bucket,
                                "mountOptions": "implicit-dirs,file-cache:max-size-mb:-1",
                                "gcsfuseLoggingSeverity": "warning",
                            },
                        },
                    }
                )

        notebook_container: dict = {
            "name": "notebook",
            "image": image,
            "command": container_command,
            "ports": [{"containerPort": container_port}],
            "volumeMounts": volume_mounts,
            "resources": {
                "requests": {
                    "cpu": str(session_spec.get("cpu_cores", 2)),
                    "memory": f"{session_spec.get('memory_gb', 4)}Gi",
                },
                "limits": {
                    "cpu": str(session_spec.get("cpu_cores", 2)),
                    "memory": f"{session_spec.get('memory_gb', 4)}Gi",
                },
            },
        }

        # RStudio and SSH require root for user management
        if session_type in ("rstudio", "ssh"):
            notebook_container["securityContext"] = {"runAsUser": 0}

        # GPU support for SSH work nodes
        gpu = session_spec.get("gpu")
        if gpu and session_type == "ssh":
            notebook_container["resources"]["limits"]["nvidia.com/gpu"] = "1"

        # Determine node pool based on session type
        node_pool = session_spec.get("node_pool", "interactive")

        # Git auto-commit sidecar
        containers = [notebook_container]
        git_config = session_spec.get("git_config")
        if git_config:
            git_branch = git_config.get("branch", f"session/{session_id}")
            git_user_name = git_config.get("user_name", "bioaf")
            git_user_email = git_config.get("user_email", "bioaf@localhost")
            notebooks_dir = f"{home_dir}/notebooks"
            autocommit_script = (
                f"git config --global user.name '{git_user_name}' && "
                f"git config --global user.email '{git_user_email}' && "
                "LAST_COMMIT=$(date +%s) && "
                "while true; do "
                "  sleep 60; "
                f"  cd {notebooks_dir} 2>/dev/null || continue; "
                "  NOW=$(date +%s); "
                "  DIFF=$((NOW - LAST_COMMIT)); "
                '  if [ $DIFF -ge 900 ] && [ -n "$(git status --porcelain 2>/dev/null)" ]; then '
                f'    git add -A && git commit -m "Auto-save: $(date -u +%Y-%m-%dT%H:%M:%SZ)" && git push origin {git_branch} && '
                "    LAST_COMMIT=$(date +%s); "
                "  fi; "
                "done"
            )
            containers.append(
                {
                    "name": "git-autocommit",
                    "image": "alpine/git",
                    "command": ["/bin/sh", "-c", autocommit_script],
                    "volumeMounts": [{"name": "home", "mountPath": home_dir}],
                }
            )

        # Mount GCS SA key secret into all init containers and the main container
        # so gsutil / GCP client libraries can authenticate.
        # On GKE with Workload Identity enabled, gsutil prefers the metadata
        # server over GOOGLE_APPLICATION_CREDENTIALS, so we explicitly activate
        # the service account in each init container command.
        _GCS_KEY_PATH = "/secrets/gcp/key.json"
        _GCS_AUTH_PREFIX = f"gcloud auth activate-service-account --key-file={_GCS_KEY_PATH} && "
        if has_gcs_secret:
            gcs_vol_mount = {"name": "gcp-sa-key", "mountPath": "/secrets/gcp", "readOnly": True}
            gcs_env = {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": _GCS_KEY_PATH}
            for ic in init_containers:
                ic.setdefault("volumeMounts", []).append(gcs_vol_mount)
                ic.setdefault("env", []).append(gcs_env)
                # Prepend gcloud auth activation to the shell command
                cmd: list[str] = ic.get("command", [])
                if len(cmd) >= 3 and cmd[0] == "/bin/sh" and cmd[1] == "-c":
                    cmd[2] = _GCS_AUTH_PREFIX + str(cmd[2])
            notebook_container.setdefault("env", []).append(gcs_env)
            notebook_container["volumeMounts"].append(gcs_vol_mount)
            volumes.append({"name": "gcp-sa-key", "secret": {"secretName": "bioaf-gcs-sa-key"}})

        # GCS sync sidecar: sleeps until exec'd at shutdown to sync /outputs/
        # and capture scripts. Uses google/cloud-sdk:slim which has gsutil.
        gcs_sync_mounts = [
            {"name": "outputs", "mountPath": "/outputs"},
            {"name": "home", "mountPath": home_dir},
        ]
        gcs_sync_env: list[dict] = []
        if has_gcs_secret:
            gcs_sync_mounts.append({"name": "gcp-sa-key", "mountPath": "/secrets/gcp", "readOnly": True})
            gcs_sync_env.append({"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": _GCS_KEY_PATH})
        containers.append(
            {
                "name": "gcs-sync",
                "image": "google/cloud-sdk:slim",
                "command": ["/bin/sh", "-c", "trap 'exit 0' TERM; while true; do sleep 3600; done"],
                "volumeMounts": gcs_sync_mounts,
                "env": gcs_sync_env,
                "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}},
            }
        )

        # Pod annotations -- GCS FUSE CSI driver requires this for sidecar injection
        annotations: dict[str, str] = {}
        if has_fuse_volumes:
            annotations["gke-gcsfuse/volumes"] = "true"

        # Pod manifest
        metadata: dict = {
            "name": pod_name,
            "namespace": namespace,
            "labels": {
                "bioaf.io/session": str(session_id),
                "bioaf.io/user": str(user_id),
                "bioaf.io/type": session_type,
                "bioaf.io/pool": node_pool,
            },
        }
        if annotations:
            metadata["annotations"] = annotations

        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": metadata,
            "spec": {
                "nodeSelector": {"bioaf.io/pool": node_pool},
                "tolerations": [
                    {
                        "key": "bioaf.io/pool",
                        "value": node_pool,
                        "effect": "NoSchedule",
                    }
                ],
                "serviceAccountName": "bioaf-notebook-runner",
                "initContainers": init_containers,
                "containers": containers,
                "volumes": volumes,
                "restartPolicy": "Never",
            },
        }

        # Stash gcs_home_prefix on the manifest for the caller to use
        pod_manifest["_gcs_home_prefix"] = gcs_home_prefix

        return pod_manifest

    async def _k8s_launch_session(self, session_spec: dict) -> dict:
        """Launch a notebook pod on the GKE interactive node pool."""
        await self._get_api_client_async()

        session_id = session_spec.get("session_id", 0)
        namespace = DEFAULT_NOTEBOOK_NAMESPACE

        await self.ensure_notebook_namespace(namespace, gcp_sa_email=session_spec.get("notebook_runner_sa_email", ""))

        # Ensure GCS credentials secret exists for bucket access
        has_gcs_secret = self._ensure_gcs_secret(namespace)

        pod_manifest = self._build_pod_manifest(session_spec, has_gcs_secret=has_gcs_secret)
        gcs_home_prefix = pod_manifest.pop("_gcs_home_prefix")

        # Re-derive pod_name from session_id rather than reading it off the
        # manifest dict. Reading off the dict would propagate CodeQL taint from
        # the chpasswd command embedded in pod_manifest into pod_name, which
        # then trips py/clear-text-logging at every subsequent log call.
        pod_name = f"bioaf-notebook-{session_id}"
        service_name = f"bioaf-notebook-svc-{session_id}"

        session_type = session_spec.get("session_type", "jupyter")
        if session_type == "jupyter":
            container_port = 8888
        elif session_type == "ssh":
            container_port = 22
        else:
            container_port = 8787

        core_client = self._get_k8s_core_client()
        core_client.create_namespaced_pod(namespace=namespace, body=pod_manifest)
        logger.info("Created pod %s in %s", pod_name, namespace)

        # Create Service
        service_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": service_name,
                "namespace": namespace,
                "labels": {
                    "bioaf.io/session": str(session_id),
                },
            },
            "spec": {
                "selector": {"bioaf.io/session": str(session_id)},
                "ports": [
                    {
                        "port": container_port,
                        "targetPort": container_port,
                        "protocol": "TCP",
                    }
                ],
                "type": "LoadBalancer",
            },
        }
        core_client.create_namespaced_service(namespace=namespace, body=service_manifest)
        logger.info("Created service %s in %s", service_name, namespace)

        # Launch background task to poll for pod readiness and LB IP,
        # then update the DB session record once both are available.
        asyncio.create_task(self._poll_session_ready(session_id, pod_name, service_name, namespace, container_port))

        return {
            "session_id": session_id,
            "pod_name": pod_name,
            "namespace": namespace,
            "status": "starting",
            "access_url": None,
            "gcs_home_prefix": gcs_home_prefix,
        }

    async def _poll_session_ready(
        self,
        session_id: int,
        pod_name: str,
        service_name: str,
        namespace: str,
        container_port: int,
    ) -> None:
        """Background: poll for pod readiness and LB IP, then update the DB."""
        try:
            core_client = self._get_k8s_core_client()

            # Wait for pod readiness (up to 5 minutes)
            pod_ready = False
            for _ in range(60):
                try:
                    pod = core_client.read_namespaced_pod(name=pod_name, namespace=namespace)
                    if pod.status.phase == "Running":
                        conditions = pod.status.conditions or []
                        if any(c.type == "Ready" and c.status == "True" for c in conditions):
                            pod_ready = True
                            break
                    if pod.status.phase in ("Failed", "Unknown"):
                        logger.error("Pod %s entered %s phase", pod_name, pod.status.phase)
                        reason, message = await self._classify_pod_failure_from_api(core_client, pod_name, namespace)
                        await self._update_session_in_db(
                            session_id,
                            status="failed",
                            access_url=None,
                            failure_reason=reason,
                            failure_message=message,
                        )
                        return
                except Exception:
                    pass
                await asyncio.sleep(5)

            if not pod_ready:
                logger.error("Pod %s not ready after 5 min", pod_name)
                reason, message = await self._classify_pod_failure_from_api(core_client, pod_name, namespace)
                await self._update_session_in_db(
                    session_id,
                    status="failed",
                    access_url=None,
                    failure_reason=reason,
                    failure_message=message,
                )
                return

            # Wait for LoadBalancer external IP (up to 3 minutes). The per-attempt
            # lookup is shared with the status read path via _resolve_service_url
            # (raw httpx, since the python client returns a stale ingress: None).
            # Keep the auth pre-check here so a misconfigured client fails the
            # background task loudly rather than silently spinning.
            api_client = self._get_api_client()
            if not api_client.default_headers.get("Authorization"):
                raise RuntimeError(
                    "K8s ApiClient has no Authorization header; _build_out_of_cluster_client did not set one."
                )

            access_url = None
            for attempt in range(36):
                access_url = self._resolve_service_url(service_name, namespace, container_port)
                if access_url:
                    logger.info("External URL for session %s: %s", session_id, access_url)
                    break
                await asyncio.sleep(5)

            if not access_url:
                logger.warning(
                    "LoadBalancer IP not ready for %s after 3 min",
                    service_name,
                )

            await self._update_session_in_db(session_id, status="running", access_url=access_url)

        except Exception as e:
            from app.adapters.failure_classification import FAILURE_REASON_UNKNOWN

            logger.exception("Background poll failed for session %s", session_id)
            await self._update_session_in_db(
                session_id,
                status="failed",
                access_url=None,
                failure_reason=FAILURE_REASON_UNKNOWN,
                failure_message=f"Background poll raised: {e}",
            )

    async def _classify_pod_failure_from_api(
        self,
        core_client,
        pod_name: str,
        namespace: str,
    ) -> tuple[str, str]:
        """Query the K8s API for events on `pod_name` and run them through the
        shared classifier. Returns (failure_reason, failure_message) even if
        the API call fails (falls back to "unknown")."""
        from app.adapters.failure_classification import classify_pod_failure, FAILURE_REASON_UNKNOWN

        events: list[dict] = []
        try:
            field_selector = f"involvedObject.name={pod_name}"
            evt_list = core_client.list_namespaced_event(namespace=namespace, field_selector=field_selector)
            for evt in evt_list.items or []:
                events.append(
                    {"reason": getattr(evt, "reason", "") or "", "message": getattr(evt, "message", "") or ""}
                )
        except Exception:
            logger.exception("Failed to fetch pod events for %s/%s", namespace, pod_name)
            return (FAILURE_REASON_UNKNOWN, "Pod did not become ready and pod events were unavailable.")
        return classify_pod_failure(events)

    async def _update_session_in_db(
        self,
        session_id: int,
        status: str,
        access_url: str | None,
        failure_reason: str | None = None,
        failure_message: str | None = None,
    ) -> None:
        """Update a notebook session's status, access_url, and (on failure)
        the failure_reason / failure_message taxonomy. Only writes the
        failure fields when they are set so we don't clobber a row that was
        already explained by the service layer."""
        if not self._session_factory:
            logger.warning("No session_factory, cannot update session %s in DB", session_id)
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
                logger.info(
                    "Updated session %s: status=%s access_url=%s failure_reason=%s",
                    session_id,
                    status,
                    access_url,
                    failure_reason,
                )
        except Exception:
            logger.exception("Failed to update session %s in DB", session_id)

    async def _k8s_terminate_session(
        self,
        session_id: int | str = 0,
        pod_name: str = "",
        namespace: str = DEFAULT_NOTEBOOK_NAMESPACE,
        gcs_home_prefix: str = "",
        working_bucket: str = "",
        session_type: str = "jupyter",
    ) -> dict:
        """Final git commit, sync outputs to GCS, then delete pod and service."""
        from kubernetes.stream import stream

        core_client = self._get_k8s_core_client()

        # Final git commit + push before sync-out
        git_branch = None
        git_commit = None
        if pod_name:
            try:
                git_cmd = [
                    "/bin/sh",
                    "-c",
                    "cd /home/jovyan/notebooks 2>/dev/null || cd /home/jovyan && "
                    "if [ -d .git ]; then "
                    "  git add -A && "
                    f"  git commit -m 'Session {session_id} stopped: '$(date -u +%Y-%m-%dT%H:%M:%SZ) 2>/dev/null; "
                    "  BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null); "
                    "  HASH=$(git rev-parse --short HEAD 2>/dev/null); "
                    "  git push origin $BRANCH 2>/dev/null; "
                    '  echo "GIT_BRANCH=$BRANCH"; '
                    '  echo "GIT_HASH=$HASH"; '
                    "fi",
                ]
                result = stream(
                    core_client.connect_get_namespaced_pod_exec,
                    name=pod_name,
                    namespace=namespace,
                    command=git_cmd,
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                )
                if result:
                    for line in str(result).split("\n"):
                        if line.startswith("GIT_BRANCH="):
                            git_branch = line.split("=", 1)[1].strip()
                        elif line.startswith("GIT_HASH="):
                            git_commit = line.split("=", 1)[1].strip()
                logger.info("Final git commit for pod %s: branch=%s hash=%s", pod_name, git_branch, git_commit)
            except Exception as e:
                logger.warning("Final git commit failed for pod %s: %s", pod_name, e)

        # Store git info in DB
        if git_branch and self._session_factory:
            try:
                async with self._session_factory() as db:
                    from sqlalchemy import text as sa_text

                    await db.execute(
                        sa_text(
                            "UPDATE compute_sessions SET git_branch_name = :branch, git_commit_hash = :hash "
                            "WHERE id = :id"
                        ),
                        {"branch": git_branch, "hash": git_commit, "id": session_id},
                    )
                    await db.commit()
            except Exception:
                logger.exception("Failed to store git info for compute session")

        # Sync home directory to GCS before termination
        if gcs_home_prefix and pod_name:
            try:
                sync_cmd = generate_sync_out_command(HOME_DIR, gcs_home_prefix)
                stream(
                    core_client.connect_get_namespaced_pod_exec,
                    name=pod_name,
                    namespace=namespace,
                    command=sync_cmd,
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                )
                logger.info("GCS sync-out complete for pod %s", pod_name)
            except Exception as e:
                logger.warning("GCS sync-out failed for pod %s: %s", pod_name, e)

        # Sync /outputs/ and capture scripts to GCS via the gcs-sync sidecar (ADR-040).
        # The sidecar has google/cloud-sdk:slim with gsutil; the main container may not.
        _SYNC_CONTAINER = "gcs-sync"
        _GCS_KEY = "/secrets/gcp/key.json"
        _AUTH_CMD = f"gcloud auth activate-service-account --key-file={_GCS_KEY} 2>/dev/null; "

        output_files: list[dict] = []
        gcs_output_prefix = ""
        if working_bucket and pod_name:
            gcs_output_prefix = f"gs://{working_bucket}/sessions/{session_id}/outputs/"
            gcs_scripts_prefix = f"gs://{working_bucket}/sessions/{session_id}/scripts/"

            # Determine home directory based on session type
            if session_type == "ssh":
                exec_home = "/home"
            else:
                exec_home = HOME_DIR

            # 1. Sync /outputs/ to working bucket
            try:
                outputs_shell = (
                    f"{_AUTH_CMD}"
                    f'if [ -d /outputs ] && [ "$(ls -A /outputs)" ]; then '
                    f"gsutil -m rsync -r /outputs {gcs_output_prefix}; fi"
                )
                stream(
                    core_client.connect_get_namespaced_pod_exec,
                    name=pod_name,
                    namespace=namespace,
                    container=_SYNC_CONTAINER,
                    command=["/bin/sh", "-c", outputs_shell],
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                    _request_timeout=1800,
                )
                logger.info("Outputs sync complete for pod %s", pod_name)
            except Exception as e:
                logger.warning("Outputs sync failed for pod %s: %s", pod_name, e)

            # 2. Capture notebook/script files
            try:
                scripts_shell = (
                    f"{_AUTH_CMD}"
                    f"find {exec_home} -maxdepth 3 "
                    r"\( -name '*.ipynb' -o -name '*.Rmd' -o -name '*.R' -o -name '*.py' \) "
                    "-type f "
                    f'| while read f; do gsutil cp "$f" {gcs_scripts_prefix}"$(basename "$f")"; done'
                )
                stream(
                    core_client.connect_get_namespaced_pod_exec,
                    name=pod_name,
                    namespace=namespace,
                    container=_SYNC_CONTAINER,
                    command=["/bin/sh", "-c", scripts_shell],
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                    _request_timeout=300,
                )
                logger.info("Script capture complete for pod %s", pod_name)
            except Exception as e:
                logger.warning("Script capture failed for pod %s: %s", pod_name, e)

            # 3. List all output files for registration
            try:
                list_prefix = f"gs://{working_bucket}/sessions/{session_id}/"
                list_shell = f"{_AUTH_CMD}gsutil ls -l -r {list_prefix}** 2>/dev/null || true"
                raw_output = stream(
                    core_client.connect_get_namespaced_pod_exec,
                    name=pod_name,
                    namespace=namespace,
                    container=_SYNC_CONTAINER,
                    command=["/bin/sh", "-c", list_shell],
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                    _request_timeout=60,
                )
                if raw_output:
                    from app.services.session_output_service import parse_gsutil_ls_output

                    output_files = parse_gsutil_ls_output(str(raw_output))
                    logger.info("Found %d output files for compute session", len(output_files))
            except Exception as e:
                logger.warning("Output file listing failed for pod %s: %s", pod_name, e)

        # Delete pod
        try:
            core_client.delete_namespaced_pod(name=pod_name, namespace=namespace)
            logger.info("Deleted pod %s", pod_name)
        except Exception as e:
            logger.warning("Failed to delete pod %s: %s", pod_name, e)

        # Delete service
        service_name = f"bioaf-notebook-svc-{session_id}"
        try:
            core_client.delete_namespaced_service(name=service_name, namespace=namespace)
            logger.info("Deleted notebook service")
        except Exception:
            logger.exception("Failed to delete notebook service")

        return {
            "session_id": session_id,
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "output_files": output_files,
            "gcs_output_prefix": gcs_output_prefix,
        }

    async def _k8s_get_session_status(
        self,
        session_id: int | str = 0,
        pod_name: str = "",
        namespace: str = DEFAULT_NOTEBOOK_NAMESPACE,
        session_type: str = "",
    ) -> dict:
        """Query K8s API for pod status, resolving the LoadBalancer URL if live.

        For a starting/running session this also resolves the service's external
        URL so callers read access_url off the normalized SessionStatus rather
        than reaching into the K8s client themselves.
        """
        core_client = self._get_k8s_core_client()

        try:
            pod = core_client.read_namespaced_pod(name=pod_name, namespace=namespace)
        except Exception:
            return {
                "session_id": session_id,
                "status": "unknown",
                "pod_name": pod_name,
            }

        phase = pod.status.phase
        if phase == "Running":
            conditions = pod.status.conditions or []
            ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
            status = "running" if ready else "starting"
        elif phase == "Pending":
            status = "starting"
        elif phase in ("Failed", "Unknown"):
            status = "error"
        elif phase == "Succeeded":
            status = "stopped"
        else:
            status = "unknown"

        result = {
            "session_id": session_id,
            "status": status,
            "pod_name": pod_name,
            "namespace": namespace,
        }
        if session_type:
            result["session_type"] = session_type

        # Resolve the LoadBalancer URL for live sessions so the API layer never
        # builds K8s service URLs itself.
        if status in ("running", "starting"):
            container_port = 8888 if session_type == "jupyter" else 8787
            url = self._resolve_service_url(f"bioaf-notebook-svc-{session_id}", namespace, container_port)
            if url:
                result["access_url"] = url

        return result

    def _resolve_service_url(self, service_name: str, namespace: str, container_port: int) -> str | None:
        """Single-attempt LoadBalancer external-IP lookup -> http URL, or None.

        Uses raw httpx instead of the python K8s client (which returns a stale
        ``ingress: None`` even when kubectl shows the IP). Swallows every error
        and returns None so read-path callers (status reconciliation) never
        raise on a not-yet-ready or unreachable service.
        """
        import httpx

        try:
            api_client = self._get_api_client()
            config = api_client.configuration
            auth = api_client.default_headers.get("Authorization")
            if not auth:
                return None
            svc_url = f"{config.host}/api/v1/namespaces/{namespace}/services/{service_name}"
            resp = httpx.get(
                svc_url,
                headers={"Authorization": auth},
                verify=config.ssl_ca_cert or False,
                timeout=10,
            )
            if resp.status_code == 200:
                ingress_list = resp.json().get("status", {}).get("loadBalancer", {}).get("ingress") or []
                if ingress_list:
                    ext_ip = ingress_list[0].get("ip") or ingress_list[0].get("hostname")
                    return f"http://{ext_ip}:{container_port}"
        except Exception:
            return None
        return None

    async def _k8s_list_sessions(self, filters: dict | None = None) -> list[dict]:
        """List notebook pods in the namespace."""
        core_client = self._get_k8s_core_client()
        namespace = DEFAULT_NOTEBOOK_NAMESPACE

        label_selector = "bioaf.io/pool=interactive"
        if filters and "session_type" in filters:
            label_selector += f",bioaf.io/type={filters['session_type']}"

        pod_list = core_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )

        sessions = []
        for pod in pod_list.items:
            labels = pod.metadata.labels or {}
            phase = pod.status.phase
            status = "running" if phase == "Running" else "starting"
            if phase in ("Failed", "Unknown"):
                status = "error"

            sessions.append(
                {
                    "session_id": labels.get("bioaf.io/session", ""),
                    "pod_name": pod.metadata.name,
                    "session_type": labels.get("bioaf.io/type", ""),
                    "user_id": labels.get("bioaf.io/user", ""),
                    "status": status,
                    "namespace": namespace,
                }
            )
        return sessions

    # -- Local mode implementations --

    def _local_launch_session(self, session_spec: dict) -> dict:
        session_id = f"local-{uuid.uuid4().hex[:12]}"
        session_type = session_spec.get("session_type", "jupyter")
        if session_type == "ssh":
            port = 22
        elif session_type == "jupyter":
            port = 8888
        else:
            port = 8787

        session_data = {
            "session_id": session_id,
            "status": "running",
            "url": f"http://localhost:{port}" if session_type != "ssh" else None,
            "access_url": f"ssh://localhost:{port}" if session_type == "ssh" else None,
            "session_type": session_type,
            "resource_profile": session_spec.get("resource_profile", "small"),
            "namespace": "bioaf-interactive",
            "node_pool": session_spec.get("node_pool", "bioaf-interactive"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "gcs_home_prefix": f"gs://{session_spec.get('working_bucket', 'bioaf-working')}/home/{session_spec.get('user_id', 0)}/",
        }
        _local_sessions[session_id] = session_data
        logger.info("Local mode: launched session %s (%s)", session_id, session_type)
        return session_data

    def _local_terminate_session(self, session_id: str) -> dict:
        if session_id in _local_sessions:
            _local_sessions[session_id]["status"] = "stopped"
            _local_sessions[session_id]["stopped_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("Local mode: terminated session")
        return {
            "session_id": session_id,
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "output_files": [],
            "gcs_output_prefix": "",
        }

    def _local_get_session_status(self, session_id: str) -> dict:
        if session_id in _local_sessions:
            return _local_sessions[session_id]
        return {
            "session_id": session_id,
            "status": "unknown",
        }

    def _local_list_sessions(self, filters: dict | None = None) -> list[dict]:
        sessions = list(_local_sessions.values())
        if filters:
            if "status" in filters:
                sessions = [s for s in sessions if s.get("status") == filters["status"]]
            if "session_type" in filters:
                sessions = [s for s in sessions if s.get("session_type") == filters["session_type"]]
        return sessions
