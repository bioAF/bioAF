"""Kubernetes compute adapter.

Supports local/mock mode for development and real K8s API for production.
Mode is controlled by the BIOAF_COMPUTE_MODE environment variable.

When running outside the cluster (e.g., Docker Compose on a VM), the adapter
builds a K8s client from platform_config credentials (gke_cluster_endpoint,
gke_cluster_ca_cert, GCP service account key).
"""

import base64
import json as _json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone

from kubernetes import client, config

from app.adapters.base import ComputeProvider

logger = logging.getLogger("bioaf.adapters.compute.k8s")


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


def _sanitize_label_value(value: str) -> str:
    """Sanitize a string for use as a K8s label value.

    Label values must match: (([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?
    Replaces invalid characters with '-' and trims to 63 chars.
    """
    import re

    sanitized = re.sub(r"[^A-Za-z0-9\-_.]", "-", value)
    sanitized = sanitized.strip("-_.")
    return sanitized[:63]


class KubernetesComputeProvider(ComputeProvider):
    """Kubernetes compute backend with local mode for development."""

    # GCP access tokens expire after 3600s; rebuild client before that
    _TOKEN_TTL_SECONDS = 2700  # 45 minutes

    def __init__(self, session_factory=None):
        self._mode = os.environ.get("BIOAF_COMPUTE_MODE", "local")
        self._session_factory = session_factory
        self._api_client: client.ApiClient | None = None
        self._client_created_at: float = 0.0
        self._cluster_config: dict | None = None

    @property
    def is_local(self) -> bool:
        return self._mode == "local"

    async def submit_job(self, job_spec: dict) -> dict:
        if self.is_local:
            return await self._local_submit_job(job_spec)
        return await self._k8s_submit_job(job_spec)

    async def cancel_job(self, job_id: str) -> dict:
        if self.is_local:
            return await self._local_cancel_job(job_id)
        return await self._k8s_cancel_job(job_id)

    async def get_job_status(self, job_id: str) -> dict:
        if self.is_local:
            return await self._local_get_job_status(job_id)
        return await self._k8s_get_job_status(job_id)

    async def list_jobs(self, filters: dict | None = None) -> list[dict]:
        if self.is_local:
            return await self._local_list_jobs(filters)
        return await self._k8s_list_jobs(filters)

    async def get_job_logs(self, job_id: str) -> str:
        if self.is_local:
            return f"[local mode] No logs available for job {job_id}"
        return await self._k8s_get_job_logs(job_id)

    async def get_cluster_status(self) -> dict:
        if self.is_local:
            return self._local_cluster_status()
        return await self._k8s_get_cluster_status()

    async def get_cluster_metrics(self) -> dict:
        if self.is_local:
            return self._local_cluster_metrics()
        return await self._k8s_get_cluster_metrics()

    async def get_cost_estimate(self, job_spec: dict) -> dict:
        # Return the hourly node rate for the pipeline pool so the UI
        # can show $/hr and let the user reason about total cost from
        # the run duration.  Trying to predict total cost is unreliable
        # because actual cost depends on node uptime (autoscaler cooldown),
        # spot preemptions, and shared tenancy.
        status = self._local_cluster_status()
        pool = next(
            (p for p in status.get("node_pools", []) if p["name"] == "bioaf-pipelines"),
            {"machine_type": "n2-standard-4", "spot": False},
        )
        machine_type = pool["machine_type"]
        is_spot = pool.get("spot", False)
        hourly_rate = self._hourly_rate(machine_type, is_spot)

        return {
            "estimated_cost_usd": hourly_rate,
            "currency": "USD",
            "basis": f"{machine_type} {'spot' if is_spot else 'on-demand'} $/hr",
        }

    async def get_job_report(self, job_id: str) -> str:
        """Read the Nextflow HTML report from GCS."""
        if self.is_local:
            return ""
        return await self._read_gcs_report(job_id)

    async def persist_job_logs(self, job_id: str) -> bool:
        """Read pod logs and persist to GCS before pod cleanup."""
        if self.is_local:
            return False
        return await self._k8s_persist_job_logs(job_id)

    def get_raw_bucket_name(self) -> str:
        cfg = self._cluster_config or {}
        return cfg.get("raw_bucket_name", "")

    async def get_job_progress(self, job_id: str) -> dict:
        if self.is_local:
            return await self._local_get_job_progress(job_id)
        return await self._k8s_get_job_progress(job_id)

    async def get_connection_command(self, job_id: str) -> str:
        namespace = "bioaf-pipelines"
        return f"kubectl exec -it -n {namespace} job/{job_id} -- /bin/bash"

    # -- Local mode implementations --

    async def _local_submit_job(self, job_spec: dict) -> dict:
        job_id = f"local-{uuid.uuid4().hex[:12]}"
        logger.info("Local mode: submitted job %s", job_id)
        cost_estimate = await self.get_cost_estimate(job_spec)
        return {
            "job_id": job_id,
            "status": "queued",
            "estimated_cost": cost_estimate,
            "namespace": "bioaf-pipelines",
            "node_pool": "bioaf-pipelines",
        }

    async def _local_cancel_job(self, job_id: str) -> dict:
        logger.info("Local mode: cancelled job %s", job_id)
        return {
            "job_id": job_id,
            "status": "cancelled",
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _local_get_job_status(self, job_id: str) -> dict:
        return {
            "job_id": job_id,
            "status": "completed",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": 0,
        }

    async def _local_get_job_progress(self, job_id: str) -> dict:
        return {
            "percent_complete": 100.0,
            "processes": [
                {
                    "name": "LOCAL_PROCESS",
                    "status": "completed",
                    "cpu": 0.0,
                    "memory_gb": 0.0,
                    "duration_s": 0,
                }
            ],
        }

    async def _local_list_jobs(self, filters: dict | None = None) -> list[dict]:
        return []

    def _local_cluster_status(self) -> dict:
        return {
            "controller_status": "running",
            "node_pools": [
                {
                    "name": "bioaf-platform",
                    "machine_type": "e2-standard-2",
                    "min_nodes": 1,
                    "max_nodes": 3,
                    "current_nodes": 1,
                    "status": "healthy",
                },
                {
                    "name": "bioaf-pipelines",
                    "machine_type": "n2-highmem-16",
                    "min_nodes": 0,
                    "max_nodes": 20,
                    "current_nodes": 0,
                    "status": "healthy",
                    "spot": True,
                },
                {
                    "name": "bioaf-interactive",
                    "machine_type": "n2-standard-4",
                    "min_nodes": 0,
                    "max_nodes": 5,
                    "current_nodes": 0,
                    "status": "healthy",
                },
            ],
            "total_nodes": 1,
            "active_nodes": 1,
            "queue_depth": 0,
            "health": "healthy",
        }

    def _local_cluster_metrics(self) -> dict:
        from app.config import settings

        node_rate = settings.local_node_cost_hourly
        return {
            "cpu_utilization_pct": 12.5,
            "memory_utilization_pct": 28.3,
            "cost_burn_rate_hourly": node_rate,
            "node_pools": [
                {
                    "name": "bioaf-platform",
                    "cpu_utilization_pct": 25.0,
                    "memory_utilization_pct": 45.0,
                    "cost_rate_hourly": node_rate,
                },
                {
                    "name": "bioaf-pipelines",
                    "cpu_utilization_pct": 0.0,
                    "memory_utilization_pct": 0.0,
                    "cost_rate_hourly": 0.0,
                },
                {
                    "name": "bioaf-interactive",
                    "cpu_utilization_pct": 0.0,
                    "memory_utilization_pct": 0.0,
                    "cost_rate_hourly": 0.0,
                },
            ],
        }

    # -- K8s client helpers --

    async def load_cluster_config(self, force: bool = False) -> dict:
        """Read GKE cluster config from platform_config.

        Must be called during async initialization (e.g., app startup)
        so the DB query runs on the correct event loop. The result is
        cached in _cluster_config for later sync access.

        If the cached config has a "null" or missing cluster endpoint,
        re-reads from the database on each call so newly deployed
        cluster info is picked up without a restart.
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
                    "  'gke_cluster_name', 'gcp_project_id', 'gcp_region',"
                    "  'raw_bucket_name', 'k8s_pipeline_machine_type'"
                    ")"
                )
            )
            self._cluster_config = {r[0]: r[1] for r in result.fetchall()}

        # Invalidate cached API client so it rebuilds with fresh config
        if force:
            self._api_client = None

        return self._cluster_config

    def _build_out_of_cluster_client(self) -> client.ApiClient:
        """Build a K8s ApiClient using platform_config credentials.

        Requires load_cluster_config() to have been called first during
        async startup so _cluster_config is populated.
        """
        cfg = self._cluster_config or {}

        endpoint = cfg.get("gke_cluster_endpoint", "")
        ca_cert_b64 = cfg.get("gke_cluster_ca_cert", "")
        sa_key = cfg.get("gcp_service_account_key", "")

        if not endpoint or endpoint == "null":
            raise RuntimeError("No GKE cluster endpoint in platform_config. Deploy the compute stack first.")

        # Ensure endpoint has https:// scheme
        if not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}"

        # Get a GCP access token for K8s API auth
        token = _get_gcp_token(sa_key)

        # Write CA cert to a temp file for the K8s client
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

        If the cached config has a stale endpoint, reloads from platform_config
        before building the client. This handles the case where compute was
        deployed after the backend started. Also rebuilds the client when the
        GCP access token is about to expire.
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
            # Reload config in case it was stale at startup
            await self.load_cluster_config(force=True)
            try:
                self._api_client = self._build_out_of_cluster_client()
                logger.info(
                    "K8s client built for endpoint %s", (self._cluster_config or {}).get("gke_cluster_endpoint")
                )
            except Exception:
                logger.exception("Failed to build out-of-cluster K8s client")
                raise

        return self._api_client

    def _get_api_client(self) -> client.ApiClient:
        """Get or create a K8s ApiClient, trying incluster first (sync version)."""
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
                    "K8s client built for endpoint %s", (self._cluster_config or {}).get("gke_cluster_endpoint")
                )
            except Exception:
                logger.exception("Failed to build out-of-cluster K8s client")
                raise

        return self._api_client

    def _get_k8s_core_client(self):
        """Get a Kubernetes CoreV1Api client. Tests mock this method."""
        return client.CoreV1Api(api_client=self._get_api_client())

    def _get_k8s_batch_client(self):
        """Get a Kubernetes BatchV1Api client. Tests mock this method."""
        return client.BatchV1Api(api_client=self._get_api_client())

    def _get_k8s_rbac_client(self):
        """Get a Kubernetes RbacAuthorizationV1Api client. Tests mock this method."""
        return client.RbacAuthorizationV1Api(api_client=self._get_api_client())

    _namespace_ready = False

    async def ensure_pipeline_namespace(self, namespace: str = "bioaf-pipelines") -> None:
        """Ensure the pipeline namespace, service account, and role binding exist."""
        from kubernetes.client.rest import ApiException

        core_v1 = self._get_k8s_core_client()
        rbac_v1 = self._get_k8s_rbac_client()

        # Check if namespace already exists
        try:
            core_v1.read_namespace(name=namespace)
            logger.info("Namespace %s already exists, skipping setup", namespace)
            self._namespace_ready = True
            return
        except ApiException as e:
            if e.status != 404:
                raise

        # Create namespace
        core_v1.create_namespace(
            body=client.V1Namespace(
                metadata=client.V1ObjectMeta(
                    name=namespace,
                    labels={"bioaf.io/managed": "true"},
                )
            )
        )
        logger.info("Created namespace %s", namespace)

        # Create service account
        core_v1.create_namespaced_service_account(
            namespace=namespace,
            body=client.V1ServiceAccount(
                metadata=client.V1ObjectMeta(
                    name="bioaf-pipeline-runner",
                    labels={"bioaf.io/managed": "true"},
                )
            ),
        )
        logger.info("Created service account bioaf-pipeline-runner in %s", namespace)

        # Create role binding
        rbac_v1.create_namespaced_role_binding(
            namespace=namespace,
            body=client.V1RoleBinding(
                metadata=client.V1ObjectMeta(
                    name="bioaf-pipeline-runner-binding",
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
                        name="bioaf-pipeline-runner",
                        namespace=namespace,
                    )
                ],
            ),
        )
        logger.info("Created role binding in %s", namespace)
        self._namespace_ready = True

    # -- K8s API implementations (production) --

    _GKE_STATUS_MAP = {
        0: "STATUS_UNSPECIFIED",
        1: "PROVISIONING",
        2: "RUNNING",
        3: "RECONCILING",
        4: "STOPPING",
        5: "ERROR",
        6: "DEGRADED",
    }

    def _get_gke_client(self):
        """Get a GKE ClusterManager client using platform_config credentials."""
        from google.cloud import container_v1

        cfg = self._cluster_config or {}
        sa_key = cfg.get("gcp_service_account_key", "")

        if sa_key:
            credentials = _get_gcp_credentials(sa_key)
            return container_v1.ClusterManagerClient(credentials=credentials)

        return container_v1.ClusterManagerClient()

    NEXTFLOW_IMAGE = "nextflow/nextflow:25.10.4"

    @staticmethod
    def _build_nextflow_command(
        job_spec: dict,
        report_gcs_path: str = "",
        trace_gcs_path: str = "",
    ) -> list[str]:
        """Build a Nextflow run command from the job spec.

        Translates pipeline_source, pipeline_version, parameters, and
        sample_sheet into a shell command that nextflow can execute.
        """
        pipeline_source = job_spec.get("pipeline_source", "")
        pipeline_version = job_spec.get("pipeline_version", "")
        parameters = job_spec.get("parameters", {})
        sample_sheet = job_spec.get("sample_sheet", "")

        # Log the config file before running so it appears in pod logs
        parts = ["cat /data/nextflow.config &&", "nextflow", "run", pipeline_source]

        if pipeline_version:
            parts.extend(["-r", pipeline_version])

        # Use a generated nextflow.config with K8s executor settings.
        # GKE uses containerd (no Docker daemon), so -profile docker won't work.
        parts.extend(["-c", "/data/nextflow.config"])

        if sample_sheet:
            parts.extend(["--input", "/data/samplesheet.csv"])

        # Write Nextflow HTML report to GCS so it persists after pod cleanup
        if report_gcs_path:
            parts.extend(["-with-report", report_gcs_path])

        # Write Nextflow execution trace to GCS
        if trace_gcs_path:
            parts.extend(["-with-trace", trace_gcs_path])

        # Ensure outdir is always set (nf-core pipelines require it)
        if "outdir" not in parameters:
            parameters = {**parameters, "outdir": "/data/results"}

        # Strip bioAF-internal config knobs that are not Nextflow parameters
        internal_keys = {"fusion_enabled"}

        for key, value in sorted(parameters.items()):
            if key in internal_keys:
                continue
            parts.extend([f"--{key}", str(value)])

        return ["/bin/sh", "-c", " ".join(parts)]

    # Allocatable resources per GCP machine type (after system reservations).
    # Used to set Nextflow resourceLimits so retry escalation never exceeds
    # what a single node can provide.  See ADR-042.
    _MACHINE_ALLOCATABLE: dict[str, tuple[int, int]] = {
        # (cpus, memory_gb)
        "n2-highmem-16": (14, 110),
        "n2-highmem-32": (30, 220),
        "n2-highmem-8": (7, 55),
        "n2-standard-16": (14, 55),
        "n2-standard-8": (7, 27),
        "n2-standard-4": (3, 13),
        "e2-standard-16": (14, 55),
        "e2-standard-8": (7, 27),
        "e2-highmem-16": (14, 110),
        "e2-highmem-8": (7, 55),
    }

    @staticmethod
    def _build_nextflow_k8s_config(
        namespace: str,
        has_gcs_secret: bool,
        gcs_work_dir: str | None = None,
        pipeline_machine_type: str | None = None,
    ) -> str:
        """Build a nextflow.config for K8s executor mode.

        Each Nextflow process runs as its own K8s pod. The config ensures
        pods use the right service account, have GCS credentials when
        available, and share a GCS-backed work directory so the head pod
        and process pods can exchange command scripts and data.
        """
        lines = [
            "process.executor = 'k8s'",
            f"k8s.namespace = '{namespace}'",
            "k8s.serviceAccount = 'bioaf-pipeline-runner'",
        ]

        # GCS work directory so head and process pods share files.
        # Wave + Fusion mount GCS paths as a local filesystem inside
        # process pods so they can access .command.run scripts.
        if gcs_work_dir:
            lines.append(f"workDir = '{gcs_work_dir}'")
            lines.append("wave.enabled = true")
            lines.append("fusion.enabled = true")
            lines.append("fusion.exportStorageCredentials = true")

        # Resource limits and preemption-aware retry strategy (ADR-042).
        # Prevents retry escalation from requesting more than a single
        # node can provide, and retries Spot preemptions without
        # escalating resources.
        machine = pipeline_machine_type or "n2-highmem-16"
        cpus, mem_gb = KubernetesComputeProvider._MACHINE_ALLOCATABLE.get(machine, (14, 110))
        lines.append(f"process.resourceLimits = [cpus: {cpus}, memory: '{mem_gb}.GB']")
        lines.append("process.maxRetries = 3")
        # Exit 143 (SIGTERM) and 137 (SIGKILL) from Spot preemption: retry
        # without escalating. Other failures: escalate then finish.
        lines.append(
            "process.errorStrategy = { "
            "task.exitStatus in [143, 137, 247] "
            "? (task.attempt <= 3 ? 'retry' : 'finish') "
            ": (task.attempt <= 2 ? 'retry' : 'finish') }"
        )

        # Build k8s.pod directives for secrets/env (Nextflow doesn't
        # support tolerations in k8s.pod, so node placement is left to
        # the cluster autoscaler; the head Job already targets the
        # pipeline pool via nodeSelector + toleration in the Job manifest)
        pod_directives: list[str] = []
        if has_gcs_secret:
            pod_directives.append("[secret: 'bioaf-gcs-sa-key', mountPath: '/secrets/gcp']")
            pod_directives.append("[env: 'GOOGLE_APPLICATION_CREDENTIALS', value: '/secrets/gcp/key.json']")

        if pod_directives:
            lines.append("k8s.pod = [" + ", ".join(pod_directives) + "]")

        # Docker is the default container engine for nf-core
        lines.append("docker.enabled = true")

        return "\n".join(lines)

    async def _read_gcp_credentials(self) -> tuple[str, str]:
        """Read gcp_credential_source and gcp_service_account_key fresh from platform_config.

        Cluster endpoint/CA can be cached in _cluster_config because they rarely
        change, but credentials need a per-launch read so that keys saved or
        rotated through the Settings UI take effect without a backend restart.
        """
        from sqlalchemy import text as sa_text

        if not self._session_factory:
            return "vm_default", ""

        async with self._session_factory() as session:
            result = await session.execute(
                sa_text(
                    "SELECT key, value FROM platform_config "
                    "WHERE key IN ('gcp_credential_source', 'gcp_service_account_key')"
                )
            )
            rows = {r[0]: (r[1] or "") for r in result.fetchall()}

        return rows.get("gcp_credential_source", "vm_default") or "vm_default", rows.get("gcp_service_account_key", "")

    def _ensure_gcs_secret(self, namespace: str, credential_source: str, sa_key: str) -> bool:
        """Create a K8s Secret with the GCP SA key for GCS access.

        Only mounts a key file when ``credential_source == "service_account_key"``
        -- in ``vm_default`` mode, gsutil falls back to ADC on the node.

        Returns True if the secret exists (created or already present).
        """
        import base64

        from kubernetes.client.rest import ApiException

        if credential_source != "service_account_key" or not sa_key:
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
                "data": {"key.json": base64.b64encode(sa_key.encode()).decode()},
            },
        )
        logger.info("Created GCS SA key secret in %s", namespace)
        return True

    def _ensure_ssh_key_secret(self, namespace: str, run_id: int | str, ssh_private_key: str) -> str:
        """Create a per-run K8s Secret with an SSH private key for git clone access.

        Returns the secret name so callers can mount it.
        """
        import base64

        from kubernetes.client.rest import ApiException

        core_client = self._get_k8s_core_client()
        secret_name = f"bioaf-ssh-key-{run_id}"

        try:
            core_client.read_namespaced_secret(name=secret_name, namespace=namespace)
            return secret_name
        except ApiException as e:
            if e.status != 404:
                logger.warning("Error checking SSH key secret: %s", e)
                raise

        core_client.create_namespaced_secret(
            namespace=namespace,
            body={
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": secret_name, "labels": {"bioaf.io/managed": "true"}},
                "type": "Opaque",
                "data": {"id_rsa": base64.b64encode(ssh_private_key.encode()).decode()},
            },
        )
        logger.info("Created SSH key secret %s in %s", secret_name, namespace)
        return secret_name

    async def _k8s_submit_job(self, job_spec: dict) -> dict:
        """Submit a real Kubernetes Job to the GKE cluster."""
        run_id = job_spec.get("run_id", 0)
        pipeline_name = job_spec.get("pipeline_name", "unknown")
        namespace = job_spec.get("namespace", "bioaf-pipelines")
        container_image = job_spec.get("container_image", "alpine:3.19")
        command = job_spec.get("command", [])
        stage_commands = job_spec.get("stage_commands", [])
        pipeline_source = job_spec.get("pipeline_source", "")
        sample_sheet = job_spec.get("sample_sheet", "")

        # Ensure namespace, service account, and role binding exist on first use
        if not self._namespace_ready:
            await self.ensure_pipeline_namespace(namespace)

        # Ensure GCS credentials secret exists for bucket access. Read the
        # SA key fresh from platform_config so a key saved or rotated through
        # the Settings UI takes effect without a backend restart.
        credential_source, sa_key = await self._read_gcp_credentials()
        has_gcs_secret = self._ensure_gcs_secret(namespace, credential_source, sa_key)

        job_name = f"bioaf-pipeline-{run_id}"

        # Auto-build Nextflow command when pipeline_source is set and no
        # explicit command was provided
        if pipeline_source and not command:
            container_image = self.NEXTFLOW_IMAGE

            nf_cfg = self._cluster_config or {}
            raw_bucket = nf_cfg.get("raw_bucket_name", "")

            # Write the Nextflow HTML report and execution trace directly to
            # GCS so they persist after the head pod is cleaned up.
            report_gcs_path = f"gs://{raw_bucket}/nextflow-reports/{job_name}/report.html" if raw_bucket else ""
            trace_gcs_path = f"gs://{raw_bucket}/nextflow-traces/{job_name}/trace.tsv" if raw_bucket else ""

            # Set --outdir to a GCS path so pipeline outputs persist after
            # pod cleanup.  The path mirrors the prefix that
            # _gcs_collect_outputs and _extract_metrics use to find outputs.
            experiment_id = job_spec.get("experiment_id", "unknown")
            # Derive results bucket from raw_bucket_name (bioaf-raw-X -> bioaf-results-X)
            results_bucket = (
                raw_bucket.replace("bioaf-raw-", "bioaf-results-", 1) if raw_bucket.startswith("bioaf-raw-") else ""
            )
            if results_bucket and "outdir" not in job_spec.get("parameters", {}):
                gcs_outdir = f"gs://{results_bucket}/experiments/{experiment_id}/pipeline-runs/{run_id}"
                job_spec = {**job_spec, "parameters": {**job_spec.get("parameters", {}), "outdir": gcs_outdir}}

            command = self._build_nextflow_command(
                job_spec,
                report_gcs_path=report_gcs_path,
                trace_gcs_path=trace_gcs_path,
            )

        # Build init containers for GCS input staging
        init_containers = []
        if stage_commands:
            stage_script = " && ".join(stage_commands)
            init_containers.append(
                {
                    "name": "stage-inputs",
                    "image": "google/cloud-sdk:slim",
                    "command": ["/bin/sh", "-c", stage_script],
                    "volumeMounts": [{"name": "data", "mountPath": "/data"}],
                }
            )

        # Custom pipeline: extra init containers (e.g., git clone, build).
        # Appended after stage-inputs so input data is available if needed.
        extra_init_containers = list(job_spec.get("extra_init_containers") or [])
        init_containers.extend(extra_init_containers)

        # Write sample sheet to the data volume via init container
        if sample_sheet and pipeline_source and not job_spec.get("command"):
            # Strip carriage returns that browsers/forms sometimes inject
            clean_sheet = sample_sheet.replace("\r\n", "\n").replace("\r", "\n")
            escaped_sheet = clean_sheet.replace("'", "'\\''")
            init_containers.append(
                {
                    "name": "write-samplesheet",
                    "image": "alpine:3.19",
                    "command": ["/bin/sh", "-c", f"printf '%s' '{escaped_sheet}' > /data/samplesheet.csv"],
                    "volumeMounts": [{"name": "data", "mountPath": "/data"}],
                }
            )

        # Write nextflow.config with K8s executor settings for Nextflow pipelines
        if pipeline_source and not job_spec.get("command"):
            nf_cfg = self._cluster_config or {}
            raw_bucket = nf_cfg.get("raw_bucket_name", "")
            gcs_work_dir = f"gs://{raw_bucket}/nextflow-work" if raw_bucket else None
            pipeline_machine = nf_cfg.get("k8s_pipeline_machine_type")
            nf_config = self._build_nextflow_k8s_config(namespace, has_gcs_secret, gcs_work_dir, pipeline_machine)
            # Use heredoc to avoid shell escaping issues with single quotes
            # in Nextflow config values (e.g., 'k8s', 'bioaf-pipelines')
            init_containers.append(
                {
                    "name": "write-nf-config",
                    "image": "alpine:3.19",
                    "command": [
                        "/bin/sh",
                        "-c",
                        f"cat > /data/nextflow.config << 'NFEOF'\n{nf_config}\nNFEOF",
                    ],
                    "volumeMounts": [{"name": "data", "mountPath": "/data"}],
                }
            )

        # GCS credential mounts for all containers
        gcs_volume_mount = {"name": "gcp-sa-key", "mountPath": "/secrets/gcp", "readOnly": True}
        gcs_env = {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/secrets/gcp/key.json"}

        if has_gcs_secret:
            for ic in init_containers:
                ic.setdefault("volumeMounts", []).append(gcs_volume_mount)
                ic.setdefault("env", []).append(gcs_env)

        # Custom pipeline: SSH key secret for extra init containers (e.g., git clone).
        ssh_private_key = job_spec.get("ssh_private_key")
        if ssh_private_key:
            self._ensure_ssh_key_secret(namespace, run_id, ssh_private_key)
            ssh_volume_mount = {"name": "ssh-key", "mountPath": "/root/.ssh", "readOnly": True}
            for ic in extra_init_containers:
                ic.setdefault("volumeMounts", []).append(ssh_volume_mount)

        # Build main container
        main_container = {
            "name": "pipeline",
            "image": container_image,
            "volumeMounts": [{"name": "data", "mountPath": "/data"}],
            "terminationMessagePolicy": "FallbackToLogsOnError",
        }
        if has_gcs_secret:
            main_container["volumeMounts"].append(gcs_volume_mount)
            main_container["env"] = [gcs_env]
        if command:
            main_container["command"] = command

        # Custom pipeline: extra volume mounts on main container
        has_outputs_dir = bool(job_spec.get("has_outputs_dir"))
        has_code_dir = bool(job_spec.get("has_code_dir"))
        if has_outputs_dir:
            main_container["volumeMounts"].append({"name": "outputs", "mountPath": "/outputs"})
        if has_code_dir:
            main_container["volumeMounts"].append({"name": "code", "mountPath": "/code"})

        # Custom pipeline: resource requests/limits (guaranteed QoS).
        cpu_request = job_spec.get("cpu_request")
        memory_request = job_spec.get("memory_request")
        if cpu_request or memory_request:
            requests: dict[str, str] = {}
            limits: dict[str, str] = {}
            if cpu_request:
                requests["cpu"] = str(cpu_request)
                limits["cpu"] = str(cpu_request)
            if memory_request:
                requests["memory"] = str(memory_request)
                limits["memory"] = str(memory_request)
            main_container["resources"] = {"requests": requests, "limits": limits}

        # Custom pipeline: extra environment variables on main container.
        extra_env = list(job_spec.get("extra_env") or [])
        if extra_env:
            main_container.setdefault("env", []).extend(extra_env)

        # Custom pipeline: working directory on main container.
        working_dir = job_spec.get("working_dir")
        if working_dir:
            main_container["workingDir"] = working_dir

        # Build job manifest
        job_manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "namespace": namespace,
                "labels": {
                    "bioaf.io/pipeline-run": str(run_id),
                    "bioaf.io/pipeline": _sanitize_label_value(pipeline_name),
                    "bioaf.io/pool": "pipelines",
                },
            },
            "spec": {
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": 3600,
                "template": {
                    "spec": {
                        "nodeSelector": {"bioaf.io/pool": "pipelines"},
                        "tolerations": [
                            {
                                "key": "bioaf.io/pool",
                                "value": "pipelines",
                                "effect": "NoSchedule",
                            }
                        ],
                        "serviceAccountName": "bioaf-pipeline-runner",
                        "containers": [main_container],
                        "volumes": [
                            {"name": "data", "emptyDir": {"sizeLimit": "50Gi"}},
                        ]
                        + (
                            [{"name": "gcp-sa-key", "secret": {"secretName": "bioaf-gcs-sa-key"}}]
                            if has_gcs_secret
                            else []
                        )
                        + ([{"name": "outputs", "emptyDir": {"sizeLimit": "50Gi"}}] if has_outputs_dir else [])
                        + ([{"name": "code", "emptyDir": {"sizeLimit": "10Gi"}}] if has_code_dir else [])
                        + (
                            [
                                {
                                    "name": "ssh-key",
                                    "secret": {
                                        "secretName": f"bioaf-ssh-key-{run_id}",
                                        "defaultMode": 0o400,
                                    },
                                }
                            ]
                            if ssh_private_key
                            else []
                        ),
                        "restartPolicy": "Never",
                    }
                },
            },
        }

        if init_containers:
            job_manifest["spec"]["template"]["spec"]["initContainers"] = init_containers  # type: ignore[index]

        batch_client = self._get_k8s_batch_client()
        batch_client.create_namespaced_job(namespace=namespace, body=job_manifest)

        cost_estimate = await self.get_cost_estimate(job_spec)

        return {
            "job_id": job_name,
            "namespace": namespace,
            "status": "queued",
            "estimated_cost": cost_estimate,
        }

    async def _k8s_cancel_job(self, job_id: str) -> dict:
        """Delete a Kubernetes Job with background propagation."""
        batch_client = self._get_k8s_batch_client()
        namespace = "bioaf-pipelines"
        batch_client.delete_namespaced_job(
            name=job_id,
            namespace=namespace,
            propagation_policy="Background",
        )
        return {
            "job_id": job_id,
            "status": "cancelled",
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _k8s_get_job_status(self, job_id: str) -> dict:
        """Query the K8s API for Job status and translate to normalized model."""
        batch_client = self._get_k8s_batch_client()
        core_client = self._get_k8s_core_client()
        namespace = "bioaf-pipelines"

        job = batch_client.read_namespaced_job(name=job_id, namespace=namespace)

        # Get pod info
        pod_list = core_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_id}",
        )
        pod_name = pod_list.items[0].metadata.name if pod_list.items else None
        node_name = pod_list.items[0].spec.node_name if pod_list.items else None

        # Determine status from Job conditions
        status = "queued"
        if job.status.conditions:
            for condition in job.status.conditions:
                if condition.type == "Complete" and condition.status == "True":
                    status = "completed"
                    break
                if condition.type == "Failed" and condition.status == "True":
                    status = "failed"
                    break
        elif job.status.active and job.status.active > 0:
            status = "running"

        result = {
            "job_id": job_id,
            "status": status,
            "pod_name": pod_name,
            "node_name": node_name,
        }

        # Include container termination details when job has failed
        if status == "failed" and pod_list.items:
            termination_reasons = []
            pod = pod_list.items[0]
            for cs in pod.status.container_statuses or []:
                terminated = getattr(cs.state, "terminated", None)
                if terminated:
                    termination_reasons.append(
                        {
                            "container": cs.name,
                            "exit_code": terminated.exit_code,
                            "reason": terminated.reason or "",
                        }
                    )
            result["termination_reasons"] = termination_reasons

        return result

    async def _k8s_list_jobs(self, filters: dict | None = None) -> list[dict]:
        """List K8s Jobs in the pipeline namespace."""
        batch_client = self._get_k8s_batch_client()
        namespace = "bioaf-pipelines"

        job_list = batch_client.list_namespaced_job(
            namespace=namespace,
            label_selector="bioaf.io/pool=pipelines",
        )

        jobs = []
        for job in job_list.items:
            jobs.append(
                {
                    "job_id": job.metadata.name,
                    "status": "running" if job.status.active else "completed",
                    "created_at": job.metadata.creation_timestamp.isoformat()
                    if job.metadata.creation_timestamp
                    else None,
                }
            )
        return jobs

    async def _k8s_get_job_logs(self, job_id: str) -> str:
        """Retrieve logs from the pipeline pod, falling back to GCS.

        Tries the live pod first, then the persisted log in GCS (uploaded
        at pipeline exit), then pod termination status as a last resort.
        """
        core_client = self._get_k8s_core_client()
        namespace = "bioaf-pipelines"

        pod_list = core_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_id}",
        )

        # Try live pod logs first
        if pod_list.items:
            pod = pod_list.items[0]
            pod_name = pod.metadata.name
            try:
                logs = core_client.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=namespace,
                    container="pipeline",
                )
                return logs
            except Exception:
                logger.warning("Could not read logs from %s, trying GCS fallback", pod_name)

        # Fall back to Cloud Logging (GKE ships all pod stdout/stderr here)
        cloud_logs = self._read_cloud_logging(job_id)
        if cloud_logs:
            return cloud_logs

        # Fall back to persisted log in GCS
        gcs_logs = await self._read_gcs_log(job_id)
        if gcs_logs:
            return gcs_logs

        # Last resort: pod termination info
        if pod_list.items:
            return self._extract_pod_termination_info(pod_list.items[0])

        return f"No logs available for job {job_id} (pod cleaned up, no Cloud Logging or GCS log found)"

    async def _k8s_persist_job_logs(self, job_id: str) -> bool:
        """Read pod logs and persist them to GCS before the pod is cleaned up.

        Called by the completion handler while the pod still exists
        (ttlSecondsAfterFinished gives a 1-hour window). Returns True
        if the log was successfully persisted.
        """
        cfg = self._cluster_config or {}
        raw_bucket = cfg.get("raw_bucket_name", "")
        if not raw_bucket:
            return False

        core_client = self._get_k8s_core_client()
        namespace = "bioaf-pipelines"

        pod_list = core_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_id}",
        )
        if not pod_list.items:
            return False

        pod_name = pod_list.items[0].metadata.name
        try:
            logs = core_client.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container="pipeline",
            )
        except Exception:
            logger.warning("Could not read logs from %s for persistence", pod_name)
            return False

        if not logs:
            return False

        log_path = f"nextflow-traces/{job_id}/pipeline.log"
        try:
            from google.cloud import storage as gcs_storage

            sa_key = cfg.get("gcp_service_account_key", "")
            if sa_key:
                credentials = _get_gcp_credentials(sa_key)
                storage_client = gcs_storage.Client(credentials=credentials)
            else:
                storage_client = gcs_storage.Client()

            bucket = storage_client.bucket(raw_bucket)
            blob = bucket.blob(log_path)
            blob.upload_from_string(logs, content_type="text/plain")
            logger.info("Persisted pipeline log to gs://%s/%s", raw_bucket, log_path)
            return True
        except Exception:
            logger.warning("Failed to persist log to gs://%s/%s", raw_bucket, log_path)
            return False

    async def _read_gcs_log(self, job_id: str) -> str | None:
        """Read persisted pipeline.log from GCS. Returns None if unavailable."""
        cfg = self._cluster_config or {}
        raw_bucket = cfg.get("raw_bucket_name", "")
        if not raw_bucket:
            return None

        sa_key = cfg.get("gcp_service_account_key", "")
        log_path = f"nextflow-traces/{job_id}/pipeline.log"

        try:
            from google.cloud import storage as gcs_storage

            if sa_key:
                credentials = _get_gcp_credentials(sa_key)
                storage_client = gcs_storage.Client(credentials=credentials)
            else:
                storage_client = gcs_storage.Client()

            bucket = storage_client.bucket(raw_bucket)
            blob = bucket.blob(log_path)

            if not blob.exists():
                return None

            return blob.download_as_text()
        except Exception:
            logger.warning("Could not read log file gs://%s/%s", raw_bucket, log_path)
            return None

    async def _read_gcs_report(self, job_id: str) -> str:
        """Read the Nextflow HTML report from GCS. Returns empty string if unavailable."""
        cfg = self._cluster_config or {}
        raw_bucket = cfg.get("raw_bucket_name", "")
        if not raw_bucket:
            return ""

        sa_key = cfg.get("gcp_service_account_key", "")
        report_path = f"nextflow-reports/{job_id}/report.html"

        try:
            from google.cloud import storage as gcs_storage

            if sa_key:
                credentials = _get_gcp_credentials(sa_key)
                storage_client = gcs_storage.Client(credentials=credentials)
            else:
                storage_client = gcs_storage.Client()

            bucket = storage_client.bucket(raw_bucket)
            blob = bucket.blob(report_path)

            if not blob.exists():
                return ""

            return blob.download_as_text()
        except Exception:
            logger.warning("Could not read report gs://%s/%s", raw_bucket, report_path)
            return ""

    def _read_cloud_logging(self, job_id: str) -> str | None:
        """Read pipeline logs from GKE Cloud Logging.

        GKE automatically ships all container stdout/stderr to Cloud Logging.
        Logs persist for 30 days even after pods are cleaned up.
        Returns None if unavailable or no entries found.
        """
        cfg = self._cluster_config or {}
        project_id = cfg.get("gcp_project_id", "")
        if not project_id:
            return None

        sa_key = cfg.get("gcp_service_account_key", "")

        try:
            import google.cloud.logging

            if sa_key:
                credentials = _get_gcp_credentials(sa_key)
                log_client = google.cloud.logging.Client(project=project_id, credentials=credentials)
            else:
                log_client = google.cloud.logging.Client(project=project_id)

            log_filter = (
                'resource.type="k8s_container" '
                f'resource.labels.container_name="pipeline" '
                f'resource.labels.pod_name:("{job_id}")'
            )

            entries = list(log_client.list_entries(filter_=log_filter, order_by="timestamp asc"))

            if not entries:
                return None

            lines = []
            for entry in entries:
                payload = entry.payload
                if isinstance(payload, str) and payload.strip():
                    lines.append(payload)
                elif isinstance(payload, dict):
                    lines.append(str(payload.get("message", payload)))
            return "\n".join(lines) if lines else None

        except Exception:
            logger.warning("Could not read Cloud Logging for job %s", job_id)
            return None

    async def _k8s_get_job_progress(self, job_id: str) -> dict:
        """Read Nextflow trace.tsv from GCS and return normalized progress.

        The trace file is uploaded to GCS as a one-shot copy when the pipeline
        container exits, so this only returns data after completion/failure.
        """
        cfg = self._cluster_config or {}
        raw_bucket = cfg.get("raw_bucket_name", "")
        if not raw_bucket:
            return {"percent_complete": 0.0, "processes": []}

        sa_key = cfg.get("gcp_service_account_key", "")
        trace_path = f"nextflow-traces/{job_id}/trace.tsv"

        try:
            from google.cloud import storage as gcs_storage

            if sa_key:
                credentials = _get_gcp_credentials(sa_key)
                storage_client = gcs_storage.Client(credentials=credentials)
            else:
                storage_client = gcs_storage.Client()

            bucket = storage_client.bucket(raw_bucket)
            blob = bucket.blob(trace_path)

            if not blob.exists():
                return {"percent_complete": 0.0, "processes": []}

            content = blob.download_as_text()
        except Exception:
            logger.warning("Could not read trace file gs://%s/%s", raw_bucket, trace_path)
            return {"percent_complete": 0.0, "processes": []}

        return self._parse_trace_to_progress(content)

    @staticmethod
    def _parse_trace_to_progress(content: str) -> dict:
        """Parse Nextflow trace TSV content into normalized progress structure."""
        import csv
        import io

        reader = csv.DictReader(io.StringIO(content), delimiter="\t")
        rows = list(reader)

        if not rows:
            return {"percent_complete": 0.0, "processes": []}

        status_map = {
            "COMPLETED": "completed",
            "RUNNING": "running",
            "FAILED": "failed",
            "CACHED": "cached",
            "SUBMITTED": "pending",
            "PENDING": "pending",
            "ABORTED": "failed",
        }

        processes = []
        completed_count = 0
        for row in rows:
            nf_status = row.get("status", "").upper()
            mapped_status = status_map.get(nf_status, nf_status.lower())
            if mapped_status in ("completed", "cached"):
                completed_count += 1

            cpu_raw = row.get("%cpu", "0")
            try:
                cpu = float(str(cpu_raw).replace("%", "")) if cpu_raw and cpu_raw != "-" else 0.0
            except (ValueError, TypeError):
                cpu = 0.0

            mem_raw = row.get("peak_rss", "")
            memory_gb = 0.0
            if mem_raw and mem_raw != "-":
                try:
                    val = str(mem_raw).strip()
                    if "GB" in val.upper():
                        memory_gb = float(val.upper().replace("GB", "").strip())
                    elif "MB" in val.upper():
                        memory_gb = round(float(val.upper().replace("MB", "").strip()) / 1024, 2)
                except (ValueError, TypeError):
                    pass

            dur_raw = row.get("realtime", "")
            duration_s = 0
            if dur_raw and dur_raw != "-":
                try:
                    dur_str = str(dur_raw).strip()
                    if dur_str.endswith("ms"):
                        duration_s = int(float(dur_str[:-2]) / 1000)
                    elif dur_str.endswith("s") and "m" not in dur_str and "h" not in dur_str:
                        duration_s = int(float(dur_str[:-1]))
                    else:
                        secs = 0
                        if "h" in dur_str:
                            parts = dur_str.split("h")
                            secs += int(parts[0].strip()) * 3600
                            dur_str = parts[1].strip()
                        if "m" in dur_str:
                            parts = dur_str.split("m")
                            secs += int(parts[0].strip()) * 60
                            dur_str = parts[1].strip()
                        if dur_str.endswith("s"):
                            secs += int(float(dur_str[:-1]))
                        duration_s = secs
                except (ValueError, TypeError):
                    pass

            processes.append(
                {
                    "name": row.get("name", "") or row.get("process", ""),
                    "status": mapped_status,
                    "cpu": cpu,
                    "memory_gb": memory_gb,
                    "duration_s": duration_s,
                }
            )

        total = len(processes)
        pct = round(completed_count / total * 100, 1) if total > 0 else 0.0

        return {"percent_complete": pct, "processes": processes}

    @staticmethod
    def _extract_pod_termination_info(pod) -> str:
        """Build a log message from pod container status when logs are unavailable."""
        lines = [f"Pod {pod.metadata.name} - phase: {pod.status.phase}"]

        for cs in pod.status.container_statuses or []:
            terminated = getattr(cs.state, "terminated", None)
            if terminated:
                lines.append(f"Container '{cs.name}': exit_code={terminated.exit_code}, reason={terminated.reason}")
                if terminated.message:
                    lines.append(f"  message: {terminated.message}")

            waiting = getattr(cs.state, "waiting", None)
            if waiting and waiting.reason:
                lines.append(f"Container '{cs.name}' waiting: {waiting.reason}")
                if waiting.message:
                    lines.append(f"  message: {waiting.message}")

        for cs in pod.status.init_container_statuses or []:
            terminated = getattr(cs.state, "terminated", None)
            if terminated and terminated.exit_code != 0:
                lines.append(
                    f"Init container '{cs.name}': exit_code={terminated.exit_code}, reason={terminated.reason}"
                )

        return "\n".join(lines)

    async def _k8s_get_cluster_status(self) -> dict:
        """Query GKE API for real cluster status."""
        await self.load_cluster_config()
        cfg = self._cluster_config or {}
        cluster_name = cfg.get("gke_cluster_name") or os.environ.get("GKE_CLUSTER_NAME", "")
        project_id = cfg.get("gcp_project_id") or os.environ.get("GCP_PROJECT_ID", "")
        region = cfg.get("gcp_region") or os.environ.get("GCP_REGION", "us-central1")

        gke_client = self._get_gke_client()
        cluster = gke_client.get_cluster(name=f"projects/{project_id}/locations/{region}/clusters/{cluster_name}")

        node_pools = []
        total_nodes = 0
        for pool in cluster.node_pools:
            pool_status = self._GKE_STATUS_MAP.get(pool.status, "unknown")
            current = pool.initial_node_count
            total_nodes += current
            node_pools.append(
                {
                    "name": pool.name,
                    "machine_type": pool.config.machine_type,
                    "min_nodes": pool.autoscaling.min_node_count,
                    "max_nodes": pool.autoscaling.max_node_count,
                    "current_nodes": current,
                    "status": pool_status.lower() if pool_status == "RUNNING" else pool_status,
                    "spot": pool.config.spot,
                }
            )

        cluster_status_str = self._GKE_STATUS_MAP.get(cluster.status, "unknown")
        health = "healthy" if cluster_status_str == "RUNNING" else "degraded"

        return {
            "controller_status": "running" if cluster_status_str == "RUNNING" else cluster_status_str.lower(),
            "node_pools": node_pools,
            "total_nodes": total_nodes,
            "active_nodes": total_nodes,
            "queue_depth": 0,
            "health": health,
        }

    # On-demand hourly rates (USD) for common GCE machine types.
    # Source: us-central1 pricing as of 2024-Q4. Close enough for cost
    # estimation; exact billing comes from the GCP billing export.
    _GCE_HOURLY_RATES: dict[str, float] = {
        "e2-micro": 0.0084,
        "e2-small": 0.0168,
        "e2-medium": 0.0336,
        "e2-standard-2": 0.0671,
        "e2-standard-4": 0.1342,
        "e2-standard-8": 0.2684,
        "n2-standard-2": 0.0971,
        "n2-standard-4": 0.1942,
        "n2-standard-8": 0.3884,
        "n2-highmem-2": 0.1310,
        "n2-highmem-4": 0.2620,
        "n2-highmem-8": 0.5241,
        "n2-highmem-16": 1.0482,
        "n2-highcpu-4": 0.1416,
        "n2-highcpu-8": 0.2832,
    }

    _SPOT_DISCOUNT = 0.35  # spot VMs are ~65% cheaper on average

    @classmethod
    def _hourly_rate(cls, machine_type: str, spot: bool) -> float:
        """Look up the hourly rate for a GCE machine type."""
        rate = cls._GCE_HOURLY_RATES.get(machine_type, 0.10)
        if spot:
            rate *= cls._SPOT_DISCOUNT
        return round(rate, 4)

    async def _k8s_get_cluster_metrics(self) -> dict:
        """Query GKE API for cluster metrics with cost rate estimates.

        Reads cluster identity (name, project, zone) from platform_config
        so no extra environment variables are needed beyond what the deploy
        script already stores.  Falls back to env vars for compatibility.
        If the GKE API call fails, returns safe zeros so the cost endpoint
        does not 500.
        """
        await self.load_cluster_config()
        cfg = self._cluster_config or {}
        cluster_name = cfg.get("gke_cluster_name") or os.environ.get("GKE_CLUSTER_NAME", "")
        project_id = cfg.get("gcp_project_id") or os.environ.get("GCP_PROJECT_ID", "")
        region = cfg.get("gcp_region") or os.environ.get("GCP_REGION", "us-central1")

        _fallback = {
            "cpu_utilization_pct": 0.0,
            "memory_utilization_pct": 0.0,
            "cost_burn_rate_hourly": 0.0,
            "node_pools": [],
        }

        if not cluster_name or not project_id or not region:
            logger.warning(
                "Missing GKE cluster identity (name=%s, project=%s, region=%s). "
                "Store gke_cluster_name, gcp_project_id, gcp_region in platform_config.",
                cluster_name,
                project_id,
                region,
            )
            return _fallback

        try:
            gke_client = self._get_gke_client()
            cluster = gke_client.get_cluster(name=f"projects/{project_id}/locations/{region}/clusters/{cluster_name}")
        except Exception:
            logger.exception("Failed to fetch GKE cluster metrics")
            return _fallback

        total_cost = 0.0
        node_pools = []
        for pool in cluster.node_pools:
            node_count = pool.initial_node_count
            is_spot = pool.config.spot
            per_node = self._hourly_rate(pool.config.machine_type, is_spot)
            pool_cost = round(per_node * node_count, 4)
            total_cost += pool_cost
            node_pools.append(
                {
                    "name": pool.name,
                    "cpu_utilization_pct": 0.0,
                    "memory_utilization_pct": 0.0,
                    "cost_rate_hourly": pool_cost,
                }
            )

        return {
            "cpu_utilization_pct": 0.0,
            "memory_utilization_pct": 0.0,
            "cost_burn_rate_hourly": round(total_cost, 4),
            "node_pools": node_pools,
        }
