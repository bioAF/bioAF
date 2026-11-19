"""Kubernetes notebook adapter.

Supports local/mock mode for development and real K8s API for production.
Mode is the resolved settings.compute_mode (BIOAF_COMPUTE_MODE), fixed when the adapter is built.

When running outside the cluster (e.g., Docker Compose on a VM), the adapter
builds a K8s client from platform_config credentials (gke_cluster_endpoint,
gke_cluster_ca_cert) and a GCP access token from the credential injector
(impersonated bootstrap on vm_default installs, JSON key on legacy installs).
"""

import asyncio
from collections.abc import Callable
import logging
import posixpath
import uuid
from datetime import datetime, timezone

from kubernetes import client

from app.config import compute_mode_for
from app.settings_contract import ComputeMode, is_simulated
from app.platform.shell_args import ShellExpr, safe_relative_path, shell_word
from app.adapters.base import NotebookProvider
from app.adapters.capabilities import ProviderCapabilities
from app.adapters.kubernetes.connection import GkeConnection, api_client_auth_header
from app.adapters.kubernetes.service_account import ensure_service_account_annotations
from app.adapters.session_status import update_compute_session
from app.exceptions import ConnectionTargetInvalidError, ValidationError
from app.adapters.models import (
    ConnectionDescription,
    ConnectionTarget,
    NotebookWorkload,
    ResourceRelease,
    SessionInfo,
    SessionStatus,
    StoredObject,
    TerminationResult,
    WorkloadRelease,
    to_service_state,
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
        provider_details={
            "session_id": d.get("session_id"),
            "stopped_at": d.get("stopped_at"),
            **({"release": d["release"]} if d.get("release") else {}),
            **({"save_failed": True} if d.get("save_failed") else {}),
            **({"saved": True} if d.get("saved") else {}),
        },
    )


# In-memory session store for local mode
_local_sessions: dict[str, dict] = {}

DEFAULT_NOTEBOOK_NAMESPACE = "bioaf-notebooks"


def notebook_pod_name(session_id) -> str:
    """The pod name launch gives a session: one ``bioaf-notebook-`` prefix on the bare id."""
    return f"bioaf-notebook-{session_id}"


from app.adapters.notebooks.readiness import INTERACTIVE_SESSION_TYPES  # noqa: E402


def _config_text(value) -> str:
    text_value = value.strip() if isinstance(value, str) else ""
    return "" if text_value == "null" else text_value


# The Kubernetes exec client returns captured output only, never the remote exit code, so a save
# command reports its own status on a final line and is trusted only when that line says 0.
EXIT_MARKER = "BIOAF_EXIT_STATUS="


def with_exit_status(command: list[str]) -> list[str]:
    """``command`` run in a subshell, followed by a line carrying its exit status."""
    import shlex

    if len(command) == 3 and command[:2] == ["/bin/sh", "-c"]:
        script = command[2]
    else:
        script = shlex.join(command)
    return ["/bin/sh", "-c", f'( {script} ); echo "{EXIT_MARKER}$?"']


def require_clean_exit(output, what: str) -> None:
    """Raise unless ``output`` ends with this command's own zero exit status."""
    status = None
    for line in str(output or "").splitlines():
        if line.startswith(EXIT_MARKER):
            status = line[len(EXIT_MARKER) :].strip()
    if status != "0":
        raise RuntimeError(f"{what} did not succeed (exit status {status if status is not None else 'unknown'})")


def notebook_workload(
    session_id,
    user_id,
    session_type: str,
    *,
    organization_id: int | None = None,
    namespace: str | None = None,
    pod_name: str | None = None,
    service_name: str | None = None,
) -> NotebookWorkload:
    """The one way a notebook workload identity is built (reservation, launch, cleanup, stop).

    Names default to the launch naming rule; recorded names are passed in when they exist.
    """
    return NotebookWorkload(
        session_id=int(session_id),
        user_id=int(user_id or 0),
        session_type=session_type,
        namespace=namespace or DEFAULT_NOTEBOOK_NAMESPACE,
        pod_name=pod_name or notebook_pod_name(session_id),
        service_name=service_name or notebook_service_name(session_id),
        organization_id=int(organization_id) if organization_id is not None else None,
    )


def launch_workload(session_spec: dict, namespace: str) -> NotebookWorkload:
    """The workload a launch creates, with the same identity its ownership labels carry."""
    return notebook_workload(
        session_spec.get("session_id", 0),
        session_spec.get("user_id"),
        session_spec.get("session_type", "jupyter"),
        organization_id=session_spec.get("organization_id"),
        namespace=namespace,
    )


def ownership_labels(session_spec: dict) -> dict[str, str]:
    """Labels every resource of a session carries, so its owner can be verified before any delete."""
    labels = {
        "bioaf.io/managed": "true",
        "bioaf.io/session": str(session_spec.get("session_id", 0)),
        "bioaf.io/user": str(session_spec.get("user_id", "")),
        "bioaf.io/type": session_spec.get("session_type", "jupyter"),
    }
    if session_spec.get("organization_id") is not None:
        labels["bioaf.io/organization"] = str(session_spec["organization_id"])
    return labels


def notebook_service_name(session_id) -> str:
    """The LoadBalancer Service name launch gives an interactive session."""
    return f"bioaf-notebook-svc-{session_id}"


HOME_DIR = "/home/jovyan"


class KubernetesNotebookProvider(NotebookProvider):
    """Kubernetes notebook backend with local mode for development."""

    # GCP access tokens expire after 3600s; rebuild client before that
    # GKE cluster config keys this provider reads from platform_config.
    _CONFIG_KEYS = [
        "gke_cluster_endpoint",
        "gke_cluster_ca_cert",
        "gcp_credential_source",
        "gcp_service_account_key",
        "gcp_service_account_email",
        "gcp_bootstrap_sa_email",
        "gke_cluster_name",
        "gcp_project_id",
        "gcp_zone",
        # Terminal-instruction setup names the cluster's location (GKE) or region (EKS).
        "gcp_region",
        # Terminal setup: the deployed GKE location and, on EKS, the account in the context alias.
        "gke_cluster_location",
        "aws_account_id",
        "aws_region",
    ]

    def __init__(self, session_factory=None, *, mode: ComputeMode | None = None):
        self._mode = compute_mode_for(mode)
        self._session_factory = session_factory
        # Notebook launches must survive a cluster teardown + redeploy mid-process,
        # so the connection rebuilds its client when the cluster identity changes
        # (fingerprint strategy) rather than only when the token TTL elapses.
        self._gke = GkeConnection(
            config_keys=self._CONFIG_KEYS,
            session_factory=session_factory,
            invalidate_client_on_force=False,
            refresh_strategy="fingerprint",
        )
        self._namespace_ready = False
        self._pod_identity_provider = None

    @property
    def _pod_identity(self):
        """The cloud-resolved PodIdentityProvider (lazy; GKE by default)."""
        if self._pod_identity_provider is None:
            from app.adapters.pod_identity import get_pod_identity_provider

            self._pod_identity_provider = get_pod_identity_provider()
        return self._pod_identity_provider

    @property
    def _cluster_config(self):
        """Cluster config is owned by the shared GKE connection."""
        return self._gke._cluster_config

    @_cluster_config.setter
    def _cluster_config(self, value):
        self._gke._cluster_config = value

    @property
    def _api_client(self):
        return self._gke._api_client

    @_api_client.setter
    def _api_client(self, value):
        self._gke._api_client = value

    @property
    def _client_created_at(self):
        return self._gke._client_created_at

    @_client_created_at.setter
    def _client_created_at(self, value):
        self._gke._client_created_at = value

    def capabilities(self) -> ProviderCapabilities:
        """Kubernetes notebook backend supports interactive notebook sessions."""
        return ProviderCapabilities(notebooks=True)

    @property
    def is_local(self) -> bool:
        return is_simulated(self._mode)

    async def launch_session(self, session_spec: dict) -> SessionInfo:
        d = self._local_launch_session(session_spec) if self.is_local else await self._k8s_launch_session(session_spec)
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
        """Unverified legacy form for a bare canonical session id (never an already-prefixed name).

        Terminal instructions use ``describe_connection``.
        """
        from app.adapters.kubernetes.exec_access import NOTEBOOK_CONTAINER, render_exec_command

        return render_exec_command(
            pod=notebook_pod_name(session_id),
            namespace=DEFAULT_NOTEBOOK_NAMESPACE,
            container=NOTEBOOK_CONTAINER,
            context=None,
        )

    def _connection_clients(self):
        # Runs in the bounded worker thread under the provider error mapping. The fingerprint check
        # rebuilds the cached client when the freshly loaded config names another cluster, so the
        # live check reads the cluster the command names.
        api_client = self._gke.client_for_loaded_config() if self._session_factory is not None else None
        if api_client is None:
            return self._gke.core_v1(), None
        return client.CoreV1Api(api_client=api_client), None

    def _gke_cluster_manager(self):
        """GKE ClusterManager client with platform_config credentials."""
        from google.cloud import container_v1

        from app.adapters.credentials import credential_injector

        credentials = credential_injector.load_gcp_credentials(self._cluster_config or {})
        return container_v1.ClusterManagerClient(credentials=credentials)

    def _gke_list_clusters(self, parent: str, timeout: float):
        """Blocking GKE list under ``parent``: the whole response (``missing_zones`` included),
        one attempt within ``timeout``."""
        return self._gke_cluster_manager().list_clusters(parent=parent, timeout=timeout, retry=None)

    def connection_reference(self, target: ConnectionTarget) -> ConnectionTarget:
        # A legacy record with no recorded pod gets the launch naming rule applied to its id.
        return target.model_copy(
            update={
                "provider_ref": target.provider_ref or notebook_pod_name(target.record_id),
                "namespace": target.namespace or DEFAULT_NOTEBOOK_NAMESPACE,
            }
        )

    async def describe_connection(self, target: ConnectionTarget) -> ConnectionDescription:
        from app.adapters.kubernetes import exec_access

        if self.is_local:
            raise ConnectionTargetInvalidError(
                "Terminal instructions are not available in simulated compute mode.", reason="simulated_mode"
            )
        target = self.connection_reference(target)

        backend = exec_access.backend_for_cluster_auth()

        async def load(budget: Callable[[], float]) -> dict:
            # Database reads only (plus GKE location discovery off the event loop); the client is
            # refreshed for this config in _connection_clients.
            if self._session_factory is not None:
                await self._gke.load_cluster_config(force=True)
            cfg = self._cluster_config if self._cluster_config is not None else {}
            if backend == "gke":
                await exec_access.discover_gke_location(
                    cfg, list_clusters=self._gke_list_clusters, session_factory=self._session_factory, budget=budget
                )
            return cfg

        return await exec_access.resolve_connection(
            target,
            default_namespace=DEFAULT_NOTEBOOK_NAMESPACE,
            backend=backend,
            load_config=load,
            clients=self._connection_clients,
        )

    def connection_setup_guide(self) -> str:
        """The provider-selected guide for the loaded cluster settings, else the neutral fallback."""
        from app.adapters.kubernetes import exec_access
        from app.adapters.kubernetes.connection import KUBECTL_SETUP_GUIDE
        from app.exceptions import ConnectionTargetInvalidError

        try:
            backend = exec_access.backend_for_cluster_auth()
            return exec_access.cluster_access(
                backend, self._cluster_config or {}, DEFAULT_NOTEBOOK_NAMESPACE
            ).setup_guide
        except ConnectionTargetInvalidError:
            return KUBECTL_SETUP_GUIDE

    async def sync_session_storage(  # type: ignore[override]
        self,
        session_id: str,
        *,
        pod_name: str,
        namespace: str = DEFAULT_NOTEBOOK_NAMESPACE,
        gcs_prefix: str,
        local_dir: str = "/home/jovyan",
        **kwargs,
    ) -> None:
        """Exec a gsutil rsync inside the running pod to persist its home dir to GCS."""
        if self.is_local:
            return
        from kubernetes.stream import stream

        from app.adapters.registry import get_storage_adapter

        core_client = self._get_k8s_core_client()
        sync_cmd = get_storage_adapter().sync_out_command(local_dir, gcs_prefix)
        logger.info("Syncing session data to GCS from pod %s", pod_name)
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
        logger.info("GCS sync complete for pod %s", pod_name)

    # -- K8s client helpers --

    async def load_cluster_config(self, force: bool = False) -> dict:
        """Read GKE cluster config from platform_config (shared connection)."""
        return await self._gke.load_cluster_config(force=force)

    def _build_out_of_cluster_client(self) -> client.ApiClient:
        return self._gke.build_out_of_cluster_client()

    def _is_token_expired(self) -> bool:
        return self._gke.is_token_expired()

    async def _get_api_client_async(self) -> client.ApiClient:
        return await self._gke.get_api_client_async()

    def _get_api_client(self) -> client.ApiClient:
        return self._gke.get_api_client()

    def _get_k8s_core_client(self):
        """Get a Kubernetes CoreV1Api client. Tests mock this method."""
        return self._gke.core_v1()

    def _get_k8s_rbac_client(self):
        """Get a Kubernetes RbacAuthorizationV1Api client. Tests mock this method."""
        return self._gke.rbac_v1()

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

        # KSA annotations binding the pod to a cloud IAM identity (GKE Workload
        # Identity today); empty when no SA email is configured.
        sa_annotations = self._pod_identity.pod_identity_annotations(gcp_sa_email)

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

    def _patch_sa_annotation(self, core_v1, namespace: str, gcp_sa_email: str) -> None:
        """Ensure the bioaf-notebook-runner KSA carries the pod-identity binding (the upgrade path, where it
        predates pod identity)."""
        ensure_service_account_annotations(
            core_v1,
            namespace=namespace,
            service_account="bioaf-notebook-runner",
            desired=self._pod_identity.pod_identity_annotations(gcp_sa_email),
        )

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

        pod_name = notebook_pod_name(session_id)
        working_bucket = session_spec.get("working_bucket", "bioaf-working")
        # Stage data through the cloud-selected storage adapter so the staged URIs
        # and copy commands match the backend (gs:// + gcloud on GCS; s3:// + aws on
        # S3). The init/sidecar containers run the adapter's staging_image, so a
        # hardcoded gs:// / gsutil here would break a non-GCS install (e.g. on AWS
        # the amazon/aws-cli staging image has no gsutil and cannot read gs://).
        from app.adapters.registry import get_storage_adapter

        storage_adapter = get_storage_adapter()
        gcs_home_prefix = storage_adapter.build_uri(working_bucket, f"notebooks/{user_id}/")

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
                raise ValidationError("Session credentials are required for SSH work nodes")

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
        elif session_type == "headless":
            # Headless (non-interactive) execution (lit_validation Level-3): run the injected,
            # already-parameterized notebook to completion with nbconvert, writing any result files
            # to /outputs, then exit. Completion is detected off THIS container's terminated state
            # (the gcs-sync sidecar keeps the pod phase Running), and terminate_session syncs /outputs
            # the same way it does for interactive sessions. The kernel is whatever the template
            # declares (IRkernel for the R DE/DA templates; python3 otherwise).
            import base64
            import json as _json

            notebook_json = session_spec.get("notebook_json")
            if notebook_json is None:
                raise ValidationError("headless execution requires notebook_json in the session spec")
            nb_b64 = base64.b64encode(_json.dumps(notebook_json).encode()).decode()
            cell_timeout = int(session_spec.get("cell_timeout_seconds", 3600))
            container_port = 8888  # unused; a headless run exposes no server
            exec_script = (
                "set -e && mkdir -p /outputs /tmp/exec && cd /tmp/exec && "
                f"printf '%s' '{nb_b64}' | base64 -d > input.ipynb && "
                "jupyter nbconvert --to notebook --execute --output executed.ipynb "
                f"--ExecutePreprocessor.timeout={cell_timeout} input.ipynb && "
                "cp -f /tmp/exec/executed.ipynb /outputs/ 2>/dev/null || true"
            )
            container_command = ["/bin/sh", "-c", exec_script]
        else:
            # RStudio uses PAM auth -- session credentials are required.
            # User creation must happen inside the main container (not an
            # init container) because /etc/passwd and /etc/shadow are part
            # of each container's own root filesystem and are not shared.
            session_creds = session_spec.get("session_credentials")
            if not session_creds:
                raise ValidationError("Session credentials are required for RStudio sessions")

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
            gcs_home_prefix = storage_adapter.build_uri(working_bucket, f"home/{user_id}/")
        else:
            home_dir = HOME_DIR

        # The init/sidecar containers that stage data run the storage backend's
        # CLI image (CopyStager seam); GCS -> google/cloud-sdk:slim, S3 ->
        # amazon/aws-cli. The same adapter supplies read-only input mounts
        # (ReadOnlyInputMount seam).
        staging_image = storage_adapter.staging_image()

        # Build the home stage-in init container (CopyStager sync seam)
        sync_in_cmd = storage_adapter.sync_in_command(gcs_home_prefix, home_dir)
        init_container = {
            "name": "gcs-sync-in",
            "image": staging_image,
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
            # Create subdirectories and copy files preserving hierarchy, through the storage
            # adapter's copy command on every backend: each URI and path is one literal argument,
            # whatever the uploaded filename contains. A relative path that would leave /data is
            # refused before the pod exists.
            copy_cmds: list[str] = []
            inventory_lines = ["# File Inventory", "", "Files mounted at session start:", ""]
            for f in input_files:
                dest_path = f"/data/{safe_relative_path(f['relative_path'])}"
                copy_cmds.append(
                    f"mkdir -p {shell_word(posixpath.dirname(dest_path))} && "
                    f"{storage_adapter.cli_copy_in(f['gcs_uri'], dest_path)}"
                )
                inventory_lines.append(f"- /data/{f['relative_path']} (source: {f['gcs_uri']})")
            # FILE_INVENTORY.md is written by printf from one quoted argument: no heredoc, so no
            # delimiter a filename could end early, and no expansion of backticks or $().
            inventory_content = "\n".join(inventory_lines)
            copy_cmds.append(f"printf '%s\\n' {shell_word(inventory_content)} > /data/FILE_INVENTORY.md")
            data_sync_cmd = " && ".join(copy_cmds)
            init_containers.append(
                {
                    "name": "gcs-data-sync",
                    "image": staging_image,
                    "command": ["/bin/sh", "-c", data_sync_cmd],
                    "volumeMounts": [{"name": "data", "mountPath": "/data"}],
                }
            )
            volumes.append({"name": "data", "emptyDir": {"sizeLimit": "50Gi"}})
            volume_mounts.append({"name": "data", "mountPath": "/data", "readOnly": True})

        # Writable /outputs/ directory for all session types (ADR-040)
        volume_mounts.append({"name": "outputs", "mountPath": "/outputs"})
        volumes.append({"name": "outputs", "emptyDir": {"sizeLimit": "50Gi"}})

        # Pod annotations the read-only input mounts require (e.g. GCS gcsfuse
        # CSI sidecar injection); accumulated from the ReadOnlyInputMount seam.
        input_mount_annotations: dict[str, str] = {}

        # SSH work nodes get additional volumes: scratch and data mounts
        if session_type == "ssh":
            volume_mounts.append({"name": "scratch", "mountPath": "/scratch"})
            volumes.append({"name": "scratch", "emptyDir": {"sizeLimit": "100Gi"}})

            # Read-only object input mounts (ReadOnlyInputMount seam): the storage
            # backend supplies the volume / mount / pod annotations (GCS gcsfuse CSI).
            data_mount_paths = session_spec.get("data_mount_paths", [])
            for i, mount_path in enumerate(data_mount_paths):
                volume, volume_mount, pod_annotations = storage_adapter.input_mount_spec(
                    name=f"data-{i}",
                    bucket=working_bucket,
                    mount_path=f"/data/{mount_path.lstrip('/')}",
                )
                volume_mounts.append(volume_mount)
                volumes.append(volume)
                input_mount_annotations.update(pod_annotations)

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
                "image": staging_image,
                "command": ["/bin/sh", "-c", "trap 'exit 0' TERM; while true; do sleep 3600; done"],
                "volumeMounts": gcs_sync_mounts,
                "env": gcs_sync_env,
                "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}},
            }
        )

        # Pod annotations required by the read-only input mounts (e.g. the GCS
        # FUSE CSI driver's sidecar-injection annotation), supplied by the seam.
        annotations: dict[str, str] = dict(input_mount_annotations)

        # Pod manifest
        metadata: dict = {
            "name": pod_name,
            "namespace": namespace,
            "labels": {
                **ownership_labels(session_spec),
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
        pod_name = notebook_pod_name(session_id)
        service_name = notebook_service_name(session_id)

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

        # A headless run exposes no server: skip the LoadBalancer Service and the readiness poll
        # (there is nothing to reach). Its completion is polled off the notebook container's exit.
        if session_type != "headless":
            service_manifest = {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": service_name,
                    "namespace": namespace,
                    "labels": ownership_labels(session_spec),
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
            try:
                core_client.create_namespaced_service(namespace=namespace, body=service_manifest)
            except Exception:
                # The pod was accepted; release it rather than leave it holding a node.
                logger.exception("Creating service %s failed; releasing pod %s", service_name, pod_name)
                await self.release_session_resources(launch_workload(session_spec, namespace))
                raise
            logger.info("Created service %s in %s", service_name, namespace)

            # Readiness is observed by the periodic notebook pass and the session list
            # (app.services.notebook_lifecycle), not by a task tied to this process.

        return {
            "session_id": session_id,
            "pod_name": pod_name,
            "namespace": namespace,
            "status": "starting",
            "access_url": None,
            "gcs_home_prefix": gcs_home_prefix,
        }

    def reserve_workload(
        self, session_id: int, user_id: int, session_type: str, *, organization_id: int | None = None
    ) -> NotebookWorkload | None:
        """Interactive Jupyter/RStudio sessions only: headless runs and Work Nodes have their own lifecycles."""
        if self.is_local or session_type not in INTERACTIVE_SESSION_TYPES:
            return None
        return notebook_workload(session_id, user_id, session_type, organization_id=organization_id)

    @property
    def manages_workloads(self) -> bool:
        return not self.is_local

    def resolve_workload(self, ns) -> tuple[NotebookWorkload | None, str | None]:
        """The workload a recorded interactive session owns, or (None, why not).

        Uses the shared recorded-identity rules (``exec_access.recorded_notebook_identity``):
        conflicting or invalid recorded values are refused, never resolved by precedence. Only
        when no pod is recorded at all does the launch naming rule supply one; ownership is then
        still verified against the live object's labels before anything is deleted.
        """
        from app.adapters.kubernetes.exec_access import is_resource_name, recorded_notebook_identity
        from app.exceptions import ConnectionTargetInvalidError

        if self.is_local or ns.session_type not in INTERACTIVE_SESSION_TYPES:
            return None, "not a managed interactive session"
        try:
            pod_name, namespace = recorded_notebook_identity(ns)
        except ConnectionTargetInvalidError as exc:
            detail = (
                "conflicting recorded identity" if exc.reason == "conflicting_identity" else "invalid recorded identity"
            )
            logger.warning("Notebook session %s: %s; not acting on its resources", ns.id, detail)
            return None, detail
        service_name = (ns.provider_metadata or {}).get("service_name") or notebook_service_name(ns.id)
        if not is_resource_name(service_name):
            return None, "invalid recorded identity"
        return (
            notebook_workload(
                ns.id,
                ns.user_id,
                ns.session_type,
                organization_id=getattr(ns, "organization_id", None),
                namespace=namespace,
                pod_name=pod_name,
                service_name=service_name,
            ),
            None,
        )

    def recorded_workload(self, ns) -> NotebookWorkload | None:
        return self.resolve_workload(ns)[0]

    async def labelled_workloads(self) -> dict[int, list[str]]:
        """Session id -> pod and Service names carrying a ``bioaf.io/session`` label (raises on read errors)."""
        from app.adapters.notebooks.lifecycle import REQUEST_TIMEOUT_SECONDS

        if self.is_local:
            return {}
        core = self._get_k8s_core_client()

        def read() -> dict[int, list[str]]:
            found: dict[int, list[str]] = {}
            for lister in (core.list_namespaced_pod, core.list_namespaced_service):
                listing = lister(
                    namespace=DEFAULT_NOTEBOOK_NAMESPACE,
                    label_selector="bioaf.io/session",
                    _request_timeout=REQUEST_TIMEOUT_SECONDS,
                )
                for obj in listing.items or []:
                    raw = ((obj.metadata.labels or {}) if obj.metadata else {}).get("bioaf.io/session", "")
                    if raw.isdigit():
                        found.setdefault(int(raw), []).append(obj.metadata.name)
            return found

        return await asyncio.to_thread(read)

    async def workload_cluster(self) -> str | None:
        """``<project or account>/<location or region>/<cluster>``, complete, or None.

        GKE: project, deployed location (recorded, else discovered and recorded; see
        cluster_location), cluster name. EKS: account, region, cluster name. A partial identity
        is None: it cannot tell two clusters apart.
        """
        from app.adapters.kubernetes import exec_access
        from app.adapters.kubernetes.cluster_location import resolve_gke_cluster_location

        if self.is_local:
            return None
        try:
            cfg = dict(await self._gke.load_cluster_config() or {})
            name = _config_text(cfg.get("gke_cluster_name"))
            if exec_access.backend_for_cluster_auth() == "eks":
                owner, where = _config_text(cfg.get("aws_account_id")), _config_text(cfg.get("aws_region"))
            else:
                owner = _config_text(cfg.get("gcp_project_id"))
                where = ""
                if name and owner:
                    where = await resolve_gke_cluster_location(
                        cfg, list_clusters=self._gke_list_clusters, session_factory=self._session_factory
                    )
        except Exception as exc:
            logger.warning("Cluster identity unavailable for a notebook launch record (%s)", type(exc).__name__)
            return None
        return f"{owner}/{where}/{name}" if name and owner and where else None

    async def release_session_resources(self, workload: NotebookWorkload) -> WorkloadRelease:
        """Delete the session's pod and Service, each verified and independently; see lifecycle."""
        from app.adapters.notebooks.lifecycle import release_workload

        core_client = self._get_k8s_core_client()
        return await asyncio.to_thread(release_workload, core_client, workload)

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

    async def _k8s_terminate_session(
        self,
        session_id: int | str = 0,
        pod_name: str = "",
        namespace: str = DEFAULT_NOTEBOOK_NAMESPACE,
        gcs_home_prefix: str = "",
        working_bucket: str = "",
        session_type: str = "jupyter",
        user_id: int | None = None,
        ran: bool = True,
        workload: NotebookWorkload | None = None,
    ) -> dict:
        """Final git commit, sync outputs to GCS, then delete pod and service.

        ``ran=False`` (the session never became usable) skips the in-pod save steps: there is no
        work to save, and exec into a pod that never started only fails. An interactive session
        whose owner is given is released through the verified path and reports the outcome.
        """
        from kubernetes.stream import stream

        from app.adapters.notebooks.lifecycle import pod_ownership

        core_client = self._get_k8s_core_client()
        pod_name_for_exec = pod_name if ran else ""
        if workload is not None:
            pod_name, namespace = workload.pod_name, workload.namespace
            pod_name_for_exec = pod_name if ran else ""
        elif user_id is not None and session_type in INTERACTIVE_SESSION_TYPES and pod_name:
            # A caller that names the owner but not a verified workload: same builder, legacy names.
            workload = notebook_workload(session_id, user_id, session_type, namespace=namespace, pod_name=pod_name)
        if workload is not None:
            # Live ownership before the first command runs in the pod, not only before the delete.
            ownership = await asyncio.to_thread(pod_ownership, core_client, workload)
            if ownership == "foreign":
                # release_workload reports the pod blocked and deletes nothing that is not ours.
                return self._terminated(session_id, [], "", await self.release_session_resources(workload))
            if ownership == "unknown":
                unread = ResourceRelease(state="failed", detail="pod ownership could not be read")
                result = self._terminated(session_id, [], "", WorkloadRelease(pod=unread, service=unread))
                if ran:
                    # Nothing was saved: the retry must save before it deletes.
                    result["save_failed"] = True
                return result
            if ownership == "absent":
                pod_name_for_exec = ""
        save_failed = False

        # Final git commit + push before sync-out
        git_branch = None
        git_commit = None
        if pod_name_for_exec:
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

        # Sync home directory to GCS before termination (CopyStager sync seam)
        if gcs_home_prefix and pod_name_for_exec:
            try:
                from app.adapters.registry import get_storage_adapter

                sync_cmd = get_storage_adapter().sync_out_command(HOME_DIR, gcs_home_prefix)
                output = stream(
                    core_client.connect_get_namespaced_pod_exec,
                    name=pod_name,
                    namespace=namespace,
                    command=with_exit_status(sync_cmd),
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                )
                require_clean_exit(output, "home directory sync")
                logger.info("GCS sync-out complete for pod %s", pod_name)
            except Exception as e:
                logger.warning("GCS sync-out failed for pod %s: %s", pod_name, e)
                save_failed = True

        # Sync /outputs/ and capture scripts to the working bucket via the gcs-sync
        # sidecar (ADR-040). The sidecar runs the storage backend's staging_image
        # (google/cloud-sdk on GCS, amazon/aws-cli on S3), so the COMMANDS come from
        # the storage adapter, not hardcoded gsutil: a non-GCS install (e.g. AWS)
        # would otherwise run gsutil under amazon/aws-cli and silently lose outputs.
        # Auth is ambient on S3 (IRSA) -> empty; an explicit SA activation on GCS.
        from app.adapters.registry import get_storage_adapter

        storage_adapter = get_storage_adapter()
        _SYNC_CONTAINER = "gcs-sync"
        _GCS_KEY = "/secrets/gcp/key.json"
        # The key file is mounted only in service_account_key mode; under Workload Identity / IRSA
        # the activation is skipped rather than failing noisily in front of every command.
        _auth = storage_adapter.cli_auth_command(_GCS_KEY)
        _auth_prefix = f"if [ -f {shell_word(_GCS_KEY)} ]; then {_auth}; fi; " if _auth else ""
        # GCS keeps its in-pod `gsutil ls` listing (step 3); every copy goes through the adapter.
        from app.adapters.storage.gcs import GcsStorageProvider

        legacy_gcs = isinstance(storage_adapter, GcsStorageProvider)

        output_files: list[dict] = []
        gcs_output_prefix = ""
        if working_bucket and pod_name_for_exec:
            gcs_output_prefix = storage_adapter.build_uri(working_bucket, f"sessions/{session_id}/outputs/")
            gcs_scripts_prefix = storage_adapter.build_uri(working_bucket, f"sessions/{session_id}/scripts/")

            # Determine home directory based on session type
            if session_type == "ssh":
                exec_home = "/home"
            else:
                exec_home = HOME_DIR

            # 1. Sync /outputs/ to the working bucket (CopyStager sync seam).
            try:
                # sync_out_command returns ["/bin/sh", "-c", "<cli> sync ..."]; reuse
                # its inner command inside the empty-dir guard + auth prefix.
                sync_inner = storage_adapter.sync_out_command("/outputs", gcs_output_prefix)[-1]
                outputs_shell = f'{_auth_prefix}if [ -d /outputs ] && [ "$(ls -A /outputs)" ]; then {sync_inner}; fi'
                output = stream(
                    core_client.connect_get_namespaced_pod_exec,
                    name=pod_name,
                    namespace=namespace,
                    container=_SYNC_CONTAINER,
                    command=with_exit_status(["/bin/sh", "-c", outputs_shell]),
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                    _request_timeout=1800,
                )
                require_clean_exit(output, "outputs sync")
                logger.info("Outputs sync complete for pod %s", pod_name)
            except Exception as e:
                logger.warning("Outputs sync failed for pod %s: %s", pod_name, e)
                save_failed = True

            # 2. Capture notebook/script files (flat) into the scripts prefix. `find -exec` hands
            #    the names to the copy as arguments (no `read`, so newlines and backslashes
            #    survive), and the copy takes each one as data through the adapter.
            try:
                copy_each = storage_adapter.cli_copy_out_file(ShellExpr('"$f"'), gcs_scripts_prefix)
                scripts_shell = (
                    f"{_auth_prefix}"
                    f"find {shell_word(exec_home)} -maxdepth 3 "
                    r"\( -name '*.ipynb' -o -name '*.Rmd' -o -name '*.R' -o -name '*.py' \) "
                    "-type f "
                    f"-exec sh -c {shell_word(f'for f do {copy_each}; done')} sh {{}} +"
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

            # 3. List all output files for registration. GCS uses the original
            #    in-pod `gsutil ls` + parser (legacy GCP behavior, byte-identical to
            #    pre-drain); other backends list host-side via the storage adapter.
            try:
                list_prefix = storage_adapter.build_uri(working_bucket, f"sessions/{session_id}/")
                if legacy_gcs:
                    from app.adapters.notebooks.gcs_sync import parse_gsutil_ls_output

                    list_shell = f"{_auth_prefix}gsutil ls -l -r {shell_word(list_prefix)}** 2>/dev/null || true"
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
                        output_files = parse_gsutil_ls_output(str(raw_output))
                else:
                    from app.adapters.notebooks.gcs_sync import _EXCLUDED_FILENAMES, _EXCLUDED_PREFIXES

                    for obj in await storage_adapter.list_objects(list_prefix):
                        filename = obj.storage_uri.rsplit("/", 1)[-1] if "/" in obj.storage_uri else obj.storage_uri
                        if not filename or filename in _EXCLUDED_FILENAMES:
                            continue
                        if any(filename.startswith(p.rstrip("/")) for p in _EXCLUDED_PREFIXES):
                            continue
                        output_files.append(
                            {"gcs_uri": obj.storage_uri, "size_bytes": obj.size_bytes or 0, "filename": filename}
                        )
                logger.info("Found %d output files for compute session", len(output_files))
            except Exception as e:
                logger.warning("Output file listing failed for pod %s: %s", pod_name, e)

        if workload is not None:
            if save_failed and workload is not None and pod_name_for_exec:
                # The session's work was not saved: keep its pod and Service so a retry can save
                # before anything is deleted.
                logger.warning("Keeping pod %s: the session's work was not saved", pod_name)
                kept = WorkloadRelease(
                    pod=ResourceRelease(state="failed", detail="outputs not saved; pod kept for a retry"),
                    service=ResourceRelease(state="failed", detail="kept until outputs are saved"),
                )
                result = self._terminated(session_id, output_files, gcs_output_prefix, kept)
                result["save_failed"] = True
                return result
            release = await self.release_session_resources(workload)
            result = self._terminated(session_id, output_files, gcs_output_prefix, release)
            # Saved, or nothing left to save: the saves ran and succeeded, the session never
            # ran, or its pod is already gone. Only now may an owed save be cleared.
            result["saved"] = True
            return result

        # Delete pod (headless runs and callers that do not name the owner)
        try:
            core_client.delete_namespaced_pod(name=pod_name, namespace=namespace)
            logger.info("Deleted pod %s", pod_name)
        except Exception as e:
            logger.warning("Failed to delete pod %s: %s", pod_name, e)

        # Delete service. Headless runs never create a LoadBalancer Service (no readiness endpoint to
        # expose), so a delete would 404 with a noisy traceback on every headless teardown; skip it.
        # (session_type matches the pod's `bioaf.io/type` label; "headless" is the same literal used by
        # the headless-completion detection above.)
        if session_type != "headless":
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

    @staticmethod
    def _terminated(session_id, output_files, gcs_output_prefix, release: WorkloadRelease) -> dict:
        return {
            "session_id": session_id,
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "output_files": output_files,
            "gcs_output_prefix": gcs_output_prefix,
            "release": release.model_dump(),
        }

    async def _k8s_get_session_status(
        self,
        session_id: int | str = 0,
        pod_name: str = "",
        namespace: str = DEFAULT_NOTEBOOK_NAMESPACE,
        session_type: str = "",
        workload: NotebookWorkload | None = None,
    ) -> dict:
        """Query K8s API for pod status, resolving the LoadBalancer URL if live.

        For a starting/running session this also resolves the service's external
        URL so callers read access_url off the normalized SessionStatus rather
        than reaching into the K8s client themselves.
        """
        core_client = self._get_k8s_core_client()
        if session_type in INTERACTIVE_SESSION_TYPES:
            return await asyncio.to_thread(
                self._interactive_status, core_client, session_id, pod_name, namespace, session_type, workload
            )

        try:
            pod = core_client.read_namespaced_pod(name=pod_name, namespace=namespace)
        except Exception:
            return {
                "session_id": session_id,
                "status": "unknown",
                "pod_name": pod_name,
            }

        # A headless run's completion is NOT visible in the pod phase: the gcs-sync sidecar runs
        # `while true; sleep`, so with restartPolicy=Never the pod stays "Running" after the notebook
        # container exits. Key off the notebook container's terminated state instead (exit 0 -> done,
        # non-zero -> failed). Detected from the pod's own `bioaf.io/type` label so the caller need
        # not pass session_type.
        labels = (pod.metadata.labels or {}) if pod.metadata else {}
        is_headless = session_type == "headless" or labels.get("bioaf.io/type") == "headless"
        if is_headless:
            status = self._headless_container_status(pod)
        else:
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
        # builds K8s service URLs itself. Headless runs have no Service, so skip it.
        if status in ("running", "starting") and not is_headless:
            container_port = 8888 if session_type == "jupyter" else 8787
            url = self._resolve_service_url(f"bioaf-notebook-svc-{session_id}", namespace, container_port)
            if url:
                result["access_url"] = url

        return result

    def _interactive_status(
        self,
        core_client,
        session_id,
        pod_name: str,
        namespace: str,
        session_type: str,
        workload: NotebookWorkload | None = None,
    ) -> dict:
        """A Jupyter/RStudio session's status from the shared launch assessment (blocking).

        Unknown or unreadable is ``unknown``, never ``error``; a missing pod is ``error``;
        ``running`` needs the notebook container ready and a LoadBalancer address.
        """
        from app.adapters.failure_classification import classify_pod_failure
        from app.adapters.notebooks.lifecycle import (
            REQUEST_TIMEOUT_SECONDS,
            assess_pod,
            pod_is_owned,
            service_ownership,
        )

        base = {"session_id": session_id, "pod_name": pod_name, "namespace": namespace, "session_type": session_type}
        try:
            pod = core_client.read_namespaced_pod(
                name=pod_name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT_SECONDS
            )
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return {
                    **base,
                    "status": "error",
                    "workload_missing": True,
                    "failure_reason": "unknown",
                    "failure_message": "The session's workload no longer exists in the cluster.",
                }
            logger.warning("Could not read pod %s: %s", pod_name, type(exc).__name__)
            return {**base, "status": "unknown"}

        if workload is not None and not pod_is_owned(pod, workload):
            # Not this session's pod (another session, user, type or organization): never ready.
            logger.warning("Pod %s does not carry session %s's identity; not reporting it ready", pod_name, session_id)
            return {**base, "status": "unknown", "ownership": "pod mismatch"}
        assessment = assess_pod(pod, [])
        events: list[dict] = []
        if assessment.state != "ready":
            events = self._pod_events(core_client, pod_name, namespace)
            assessment = assess_pod(pod, events)
        if assessment.state == "uncertain":
            return {**base, "status": "unknown"}
        if assessment.state == "failed":
            return {
                **base,
                "status": "error",
                "failure_reason": assessment.reason,
                "failure_message": assessment.message,
            }
        if assessment.state == "ready":
            if workload is not None and service_ownership(core_client, workload) != "owned":
                # No address is published unless the Service is verified to be this session's too.
                logger.warning("Service for session %s is not verified as its own; no address published", session_id)
                return {**base, "status": "starting", "ownership": "service not verified"}
            port = 8888 if session_type == "jupyter" else 8787
            url = self._resolve_service_url(notebook_service_name(session_id), namespace, port)
            return {**base, "status": "running", "access_url": url} if url else {**base, "status": "starting"}

        result = {**base, "status": "starting"}
        cause_reason, cause_message = classify_pod_failure(events) if events else ("unknown", "")
        if cause_reason != "unknown":
            result.update(waiting_reason=cause_reason, waiting_message=(cause_message or "")[:500])
        return result

    @staticmethod
    def _pod_events(core_client, pod_name: str, namespace: str) -> list[dict]:
        from app.adapters.notebooks.lifecycle import REQUEST_TIMEOUT_SECONDS

        try:
            listing = core_client.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={pod_name}",
                _request_timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning("Could not read events for pod %s", pod_name)
            return []
        return [
            {"reason": getattr(e, "reason", "") or "", "message": getattr(e, "message", "") or ""}
            for e in (getattr(listing, "items", None) or [])
        ]

    @staticmethod
    def _headless_container_status(pod) -> str:
        """Map a headless pod's `notebook` container terminated state to a session status.

        The pod phase is unreliable for headless (the sleeping gcs-sync sidecar keeps it Running),
        so we read the notebook container directly: terminated exit 0 -> stopped (done), non-zero ->
        error, still running -> running, otherwise starting.
        """
        statuses = (pod.status.container_statuses or []) if pod.status else []
        nb = next((c for c in statuses if c.name == "notebook"), None)
        if nb is None or nb.state is None:
            return "starting"
        terminated = getattr(nb.state, "terminated", None)
        if terminated is not None:
            return "stopped" if terminated.exit_code == 0 else "error"
        if getattr(nb.state, "running", None) is not None:
            return "running"
        return "starting"

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
            auth = api_client_auth_header(api_client)
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
