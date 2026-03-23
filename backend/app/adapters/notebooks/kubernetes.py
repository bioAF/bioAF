"""Kubernetes notebook adapter.

Supports local/mock mode for development and real K8s API for production.
Mode is controlled by the BIOAF_COMPUTE_MODE environment variable.

When running outside the cluster (e.g., Docker Compose on a VM), the adapter
builds a K8s client from platform_config credentials (gke_cluster_endpoint,
gke_cluster_ca_cert, GCP service account key).
"""

import asyncio
import base64
import json as _json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone

from kubernetes import client, config

from app.adapters.base import NotebookProvider
from app.services.session_persistence import (
    generate_sync_in_command,
    generate_sync_out_command,
)

logger = logging.getLogger("bioaf.adapters.notebooks.k8s")


def _get_gcp_credentials(service_account_key_json: str):
    """Build GCP credentials from a service account key JSON string."""
    from google.oauth2 import service_account

    key_data = _json.loads(service_account_key_json)
    return service_account.Credentials.from_service_account_info(
        key_data,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def _get_gcp_token(service_account_key_json: str) -> str:
    """Exchange a GCP service account key for an access token."""
    import google.auth.transport.requests

    credentials = _get_gcp_credentials(service_account_key_json)
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
        self._cluster_config: dict | None = None
        self._namespace_ready = False

    @property
    def is_local(self) -> bool:
        return self._mode == "local"

    async def launch_session(self, session_spec: dict) -> dict:
        if self.is_local:
            return self._local_launch_session(session_spec)
        return await self._k8s_launch_session(session_spec)

    async def terminate_session(self, session_id: str, **kwargs) -> dict:
        if self.is_local:
            return self._local_terminate_session(session_id)
        return await self._k8s_terminate_session(session_id=session_id, **kwargs)

    async def get_session_status(self, session_id: str, **kwargs) -> dict:
        if self.is_local:
            return self._local_get_session_status(session_id)
        return await self._k8s_get_session_status(session_id=session_id, **kwargs)

    async def list_sessions(self, filters: dict | None = None) -> list[dict]:
        if self.is_local:
            return self._local_list_sessions(filters)
        return await self._k8s_list_sessions(filters)

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

        from sqlalchemy import text as sa_text

        if not self._session_factory:
            self._cluster_config = {}
            return self._cluster_config

        async with self._session_factory() as session:
            result = await session.execute(
                sa_text(
                    "SELECT key, value FROM platform_config "
                    "WHERE key IN ("
                    "  'gke_cluster_endpoint', 'gke_cluster_ca_cert',"
                    "  'gcp_credential_source', 'gcp_service_account_key',"
                    "  'gke_cluster_name', 'gcp_project_id', 'gcp_zone'"
                    ")"
                )
            )
            self._cluster_config = {r[0]: r[1] for r in result.fetchall()}

        if force:
            self._api_client = None

        return self._cluster_config

    def _build_out_of_cluster_client(self) -> client.ApiClient:
        """Build a K8s ApiClient using platform_config credentials.

        Requires load_cluster_config() to have been called first.
        """
        cfg = self._cluster_config or {}

        endpoint = cfg.get("gke_cluster_endpoint", "")
        ca_cert_b64 = cfg.get("gke_cluster_ca_cert", "")
        sa_key = cfg.get("gcp_service_account_key", "")

        if not endpoint or endpoint == "null":
            raise RuntimeError("No GKE cluster endpoint in platform_config. Deploy the compute stack first.")

        if not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}"

        token = _get_gcp_token(sa_key)

        ca_cert_bytes = base64.b64decode(ca_cert_b64)
        ca_file = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
        ca_file.write(ca_cert_bytes)
        ca_file.close()

        configuration = client.Configuration()
        configuration.host = endpoint
        configuration.ssl_ca_cert = ca_file.name
        configuration.api_key = {"authorization": f"Bearer {token}"}

        self._client_created_at = time.monotonic()
        return client.ApiClient(configuration)

    def _is_token_expired(self) -> bool:
        """Check if the cached GCP access token is older than the TTL."""
        if self._client_created_at == 0.0:
            return False
        return (time.monotonic() - self._client_created_at) > self._TOKEN_TTL_SECONDS

    async def _get_api_client_async(self) -> client.ApiClient:
        """Get or create a K8s ApiClient, trying incluster first.

        Falls back to platform_config credentials when not running in a pod.
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
            await self.load_cluster_config(force=True)
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

    async def ensure_notebook_namespace(self, namespace: str = DEFAULT_NOTEBOOK_NAMESPACE) -> None:
        """Ensure the notebook namespace and service account exist."""
        from kubernetes.client.rest import ApiException

        if self._namespace_ready:
            return

        core_v1 = self._get_k8s_core_client()
        rbac_v1 = self._get_k8s_rbac_client()

        try:
            core_v1.read_namespace(name=namespace)
            logger.info("Namespace %s already exists, skipping setup", namespace)
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

        core_v1.create_namespaced_service_account(
            namespace=namespace,
            body=client.V1ServiceAccount(
                metadata=client.V1ObjectMeta(
                    name="bioaf-notebook-runner",
                    labels={"bioaf.io/managed": "true"},
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

    # -- K8s API implementations (production) --

    async def _k8s_launch_session(self, session_spec: dict) -> dict:
        """Launch a notebook pod on the GKE interactive node pool."""
        # Ensure API client is initialized (handles incluster vs out-of-cluster)
        await self._get_api_client_async()

        session_id = session_spec.get("session_id", 0)
        session_type = session_spec.get("session_type", "jupyter")
        user_id = session_spec.get("user_id", 0)
        namespace = DEFAULT_NOTEBOOK_NAMESPACE

        await self.ensure_notebook_namespace(namespace)

        pod_name = f"bioaf-notebook-{session_id}"
        service_name = f"bioaf-notebook-svc-{session_id}"
        gcs_home_prefix = f"gs://bioaf-working/notebooks/{user_id}/"

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

            startup_script = (
                f"useradd -m -d {HOME_DIR} -s /bin/bash {cred_username} || true && "
                f"{chpasswd_cmd} && "
                f"chown -R {cred_username}:{cred_username} {HOME_DIR} && "
                f"exec /usr/lib/rstudio-server/bin/rserver "
                f"--www-address=0.0.0.0 --www-port={container_port} --server-daemonize=0"
            )
            container_command = ["/bin/sh", "-c", startup_script]

        # Build GCS sync init container
        sync_in_cmd = generate_sync_in_command(gcs_home_prefix, HOME_DIR)
        init_container = {
            "name": "gcs-sync-in",
            "image": "google/cloud-sdk:slim",
            "command": sync_in_cmd,
            "volumeMounts": [{"name": "home", "mountPath": HOME_DIR}],
        }

        init_containers = [init_container]

        # Build notebook container
        image = session_spec.get("image", "bioaf-scrna:latest")
        notebook_container: dict = {
            "name": "notebook",
            "image": image,
            "command": container_command,
            "ports": [{"containerPort": container_port}],
            "volumeMounts": [{"name": "home", "mountPath": HOME_DIR}],
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

        # RStudio Server requires root to manage sessions and write pid files.
        if session_type == "rstudio":
            notebook_container["securityContext"] = {"runAsUser": 0}

        # Pod manifest
        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace,
                "labels": {
                    "bioaf.io/session": str(session_id),
                    "bioaf.io/user": str(user_id),
                    "bioaf.io/type": session_type,
                    "bioaf.io/pool": "interactive",
                },
            },
            "spec": {
                "nodeSelector": {"bioaf.io/pool": "interactive"},
                "tolerations": [
                    {
                        "key": "bioaf.io/pool",
                        "value": "interactive",
                        "effect": "NoSchedule",
                    }
                ],
                "serviceAccountName": "bioaf-notebook-runner",
                "initContainers": init_containers,
                "containers": [notebook_container],
                "volumes": [
                    {"name": "home", "emptyDir": {"sizeLimit": "10Gi"}},
                ],
                "restartPolicy": "Never",
            },
        }

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
                        await self._update_session_in_db(session_id, status="failed", access_url=None)
                        return
                except Exception:
                    pass
                await asyncio.sleep(5)

            if not pod_ready:
                logger.error("Pod %s not ready after 5 min", pod_name)
                await self._update_session_in_db(session_id, status="failed", access_url=None)
                return

            # Wait for LoadBalancer external IP (up to 3 minutes)
            # Use raw httpx instead of python client (client returns stale
            # ingress: None even when kubectl shows the IP).
            import httpx

            api_client = self._get_api_client()
            config = api_client.configuration
            svc_url = f"{config.host}/api/v1/namespaces/{namespace}/services/{service_name}"
            headers = {"Authorization": list(config.api_key.values())[0]}

            access_url = None
            for attempt in range(36):
                try:
                    resp = httpx.get(
                        svc_url,
                        headers=headers,
                        verify=config.ssl_ca_cert or False,
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        ingress_list = resp.json().get("status", {}).get("loadBalancer", {}).get("ingress") or []
                        if ingress_list:
                            ext_ip = ingress_list[0].get("ip") or ingress_list[0].get("hostname")
                            access_url = f"http://{ext_ip}:{container_port}"
                            logger.info(
                                "External URL for session %s: %s",
                                session_id,
                                access_url,
                            )
                            break
                except Exception:
                    pass
                await asyncio.sleep(5)

            if not access_url:
                logger.warning(
                    "LoadBalancer IP not ready for %s after 3 min",
                    service_name,
                )

            await self._update_session_in_db(session_id, status="running", access_url=access_url)

        except Exception:
            logger.exception("Background poll failed for session %s", session_id)
            await self._update_session_in_db(session_id, status="failed", access_url=None)

    async def _update_session_in_db(
        self,
        session_id: int,
        status: str,
        access_url: str | None,
    ) -> None:
        """Update a notebook session's status and access_url in the DB."""
        if not self._session_factory:
            logger.warning("No session_factory, cannot update session %s in DB", session_id)
            return

        try:
            async with self._session_factory() as db:
                from sqlalchemy import text

                await db.execute(
                    text("UPDATE notebook_sessions SET status = :status, access_url = :url WHERE id = :id"),
                    {"status": status, "url": access_url, "id": session_id},
                )
                await db.commit()
                logger.info(
                    "Updated session %s: status=%s access_url=%s",
                    session_id,
                    status,
                    access_url,
                )
        except Exception:
            logger.exception("Failed to update session %s in DB", session_id)

    async def _k8s_terminate_session(
        self,
        session_id: int | str = 0,
        pod_name: str = "",
        namespace: str = DEFAULT_NOTEBOOK_NAMESPACE,
        gcs_home_prefix: str = "",
    ) -> dict:
        """Sync to GCS, then delete pod and service."""
        from kubernetes.stream import stream

        core_client = self._get_k8s_core_client()

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
            logger.info("Deleted service %s", service_name)
        except Exception as e:
            logger.warning("Failed to delete service %s: %s", service_name, e)

        return {
            "session_id": session_id,
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _k8s_get_session_status(
        self,
        session_id: int | str = 0,
        pod_name: str = "",
        namespace: str = DEFAULT_NOTEBOOK_NAMESPACE,
    ) -> dict:
        """Query K8s API for pod status."""
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

        return {
            "session_id": session_id,
            "status": status,
            "pod_name": pod_name,
            "namespace": namespace,
        }

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
        port = 8888 if session_type == "jupyter" else 8787

        session_data = {
            "session_id": session_id,
            "status": "running",
            "url": f"http://localhost:{port}",
            "session_type": session_type,
            "resource_profile": session_spec.get("resource_profile", "small"),
            "namespace": "bioaf-interactive",
            "node_pool": "bioaf-interactive",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _local_sessions[session_id] = session_data
        logger.info("Local mode: launched session %s (%s)", session_id, session_type)
        return session_data

    def _local_terminate_session(self, session_id: str) -> dict:
        if session_id in _local_sessions:
            _local_sessions[session_id]["status"] = "stopped"
            _local_sessions[session_id]["stopped_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("Local mode: terminated session %s", session_id)
        return {
            "session_id": session_id,
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
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
