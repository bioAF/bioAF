"""Kubernetes compute adapter.

Supports local/mock mode for development and real K8s API for production.
Mode is controlled by the BIOAF_COMPUTE_MODE environment variable.

When running outside the cluster (e.g., Docker Compose on a VM), the adapter
builds a K8s client from platform_config credentials (gke_cluster_endpoint,
gke_cluster_ca_cert) and a GCP access token from credential_injector
(impersonated bootstrap on vm_default installs, JSON key on legacy installs).
"""

import logging
import os
import time
import uuid
from datetime import datetime, timezone

from kubernetes import client

from app.adapters.base import ComputeProvider
from app.adapters.capabilities import ProviderCapabilities
from app.adapters.compute.cluster_autoscaler import build_cluster_autoscaler_manifests
from app.adapters.kubernetes.connection import GkeConnection
from app.adapters.models import (
    ClusterDetail,
    ClusterMetrics,
    ClusterProbe,
    ClusterStatus,
    CostEstimate,
    JobProgress,
    JobStatus,
    JobSubmitResult,
    NodePoolMetrics,
    NodePoolStatus,
    ProcessInfo,
    TerminationReason,
    to_job_state,
)
from app.pipeline import nextflow_trace

logger = logging.getLogger("bioaf.adapters.compute.k8s")

_JOB_STATUS_KEYS = {
    "job_id",
    "status",
    "started_at",
    "completed_at",
    "created_at",
    "exit_code",
    "termination_reasons",
}


def _job_submit_result_from_dict(d: dict) -> JobSubmitResult:
    ec = d.get("estimated_cost")
    return JobSubmitResult(
        job_id=d["job_id"],
        status=to_job_state(d.get("status")),
        estimated_cost=CostEstimate(**ec) if isinstance(ec, dict) else ec,
        provider_details={k: v for k, v in d.items() if k not in {"job_id", "status", "estimated_cost"}},
    )


def _job_status_from_dict(d: dict) -> JobStatus:
    return JobStatus(
        job_id=str(d.get("job_id", "")),
        status=to_job_state(d.get("status")),
        started_at=d.get("started_at"),
        completed_at=d.get("completed_at"),
        created_at=d.get("created_at"),
        exit_code=d.get("exit_code"),
        termination_reasons=[TerminationReason(**r) for r in d.get("termination_reasons", [])],
        provider_details={k: v for k, v in d.items() if k not in _JOB_STATUS_KEYS},
    )


def _cluster_status_from_dict(d: dict) -> ClusterStatus:
    return ClusterStatus(
        controller_status=d.get("controller_status"),
        node_pools=[NodePoolStatus(**p) for p in d.get("node_pools", [])],
        total_nodes=d.get("total_nodes", 0),
        active_nodes=d.get("active_nodes", 0),
        queue_depth=d.get("queue_depth", 0),
        health=d.get("health"),
    )


def _cluster_metrics_from_dict(d: dict) -> ClusterMetrics:
    return ClusterMetrics(
        cpu_utilization_pct=d.get("cpu_utilization_pct"),
        memory_utilization_pct=d.get("memory_utilization_pct"),
        cost_burn_rate_hourly=d.get("cost_burn_rate_hourly"),
        node_pools=[NodePoolMetrics(**p) for p in d.get("node_pools", [])],
    )


def _job_progress_from_dict(d: dict) -> JobProgress:
    return JobProgress(
        percent_complete=d.get("percent_complete", 0.0),
        processes=[ProcessInfo(**p) for p in d.get("processes", [])],
    )


def _resolve_cfg(cfg: dict, key: str, env_key: str, default: str = "") -> str:
    """Read a config value, treating the literal "null" sentinel as missing.

    Some platform_config rows are written as the string "null" when their
    upstream Terraform output was empty (see stack_deployment._set_config).
    This helper normalizes those back to "" so callers can use truthy checks.
    """
    val = cfg.get(key) or os.environ.get(env_key, "") or default
    if val == "null":
        return default
    return val


def _load_gcp_credentials(cfg: dict):
    """Return GCP credentials for the given platform_config dict.

    Routes through credential_injector so vm_default installs get
    impersonated bootstrap credentials and legacy installs get
    service_account.Credentials from the stored JSON key.
    """
    from app.adapters.credentials import credential_injector

    return credential_injector.load_gcp_credentials(cfg)


def _sanitize_label_value(value: str) -> str:
    """Sanitize a string for use as a K8s label value.

    Label values must match: (([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?
    Replaces invalid characters with '-' and trims to 63 chars.
    """
    import re

    sanitized = re.sub(r"[^A-Za-z0-9\-_.]", "-", value)
    sanitized = sanitized.strip("-_.")
    return sanitized[:63]


def _pod_log_to_text(value) -> str:
    """Coerce a ``read_namespaced_pod_log`` payload to ``str``.

    Newer kubernetes Python clients (>= ~31) return ``bytes`` from
    ``read_namespaced_pod_log``; older ones return ``str``. If bytes leak through
    the adapter's ``-> str`` contract, the log renders as a Python bytes repr
    (``b"...\\n..."`` with literal ``\\n``) instead of clean text. Decode
    defensively so the contract holds regardless of the installed client version.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _resolve_results_bucket(cfg: dict) -> str | None:
    """Resolve the pipeline results bucket from cluster config.

    Prefers the explicit ``results_bucket_name`` (set by Terraform); falls back to
    deriving it from ``raw_bucket_name`` (bioaf-raw-X -> bioaf-results-X) for
    installs where the raw bucket was populated before the results bucket. Returns
    None when neither yields a bucket. Sync mirror of
    ``qc.extractors.gcs_helpers.get_results_bucket`` for the config-dict the compute
    adapter holds (it has no DB session at job-build time).
    """
    results = cfg.get("results_bucket_name")
    if results and results != "null":
        return results
    raw = cfg.get("raw_bucket_name", "")
    if raw and raw.startswith("bioaf-raw-"):
        return raw.replace("bioaf-raw-", "bioaf-results-", 1)
    return None


class KubernetesComputeProvider(ComputeProvider):
    """Kubernetes compute backend with local mode for development."""

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
        "gcp_region",
        # aws_region lets the EKS ClusterAuth provider mint a region-correct STS
        # token; additive and ignored on GCP (the EKS endpoint/CA reuse the
        # gke_cluster_* keys above). pipeline_runner_role_arn is the AWS IRSA
        # identity the pipeline KSA is annotated with (GCP derives a GSA email
        # from project_id instead).
        "aws_region",
        "pipeline_runner_role_arn",
        # The in-cluster Cluster Autoscaler's IRSA role + optional image override.
        # AWS-only: absent on GCP (GKE autoscales node pools natively), so the
        # lazy ensure below is naturally gated by this key's presence.
        "cluster_autoscaler_role_arn",
        "cluster_autoscaler_image",
        "raw_bucket_name",
        "results_bucket_name",
        "k8s_pipeline_machine_type",
        # The pool's node disk. Read here so the generated nextflow.config can size its
        # ephemeral-storage request against the disk the tasks will actually land on.
        "k8s_pipeline_disk_size_gb",
    ]

    def __init__(self, session_factory=None):
        self._mode = os.environ.get("BIOAF_COMPUTE_MODE", "local")
        self._gke = GkeConnection(
            config_keys=self._CONFIG_KEYS,
            session_factory=session_factory,
            invalidate_client_on_force=True,
            refresh_strategy="simple",
        )
        self._pod_identity_provider = None

    @property
    def _pod_identity(self):
        """The cloud-resolved PodIdentityProvider (lazy; GKE by default)."""
        if self._pod_identity_provider is None:
            from app.adapters.pod_identity import get_pod_identity_provider

            self._pod_identity_provider = get_pod_identity_provider()
        return self._pod_identity_provider

    @property
    def _session_factory(self):
        return self._gke._session_factory

    @_session_factory.setter
    def _session_factory(self, value):
        self._gke._session_factory = value

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

    @property
    def is_local(self) -> bool:
        return self._mode == "local"

    def capabilities(self) -> ProviderCapabilities:
        """Kubernetes compute supports cost estimation, autoscaling, exec,
        spot/preemption retry, and job reports."""
        return ProviderCapabilities(
            cost_estimation=True,
            autoscaling=True,
            ssh_exec=True,
            spot_retry=True,
            job_report=True,
        )

    async def submit_job(self, job_spec: dict) -> JobSubmitResult:
        d = await self._local_submit_job(job_spec) if self.is_local else await self._k8s_submit_job(job_spec)
        return _job_submit_result_from_dict(d)

    async def cancel_job(self, job_id: str) -> JobStatus:
        d = await self._local_cancel_job(job_id) if self.is_local else await self._k8s_cancel_job(job_id)
        return _job_status_from_dict(d)

    async def get_job_status(self, job_id: str) -> JobStatus:
        d = await self._local_get_job_status(job_id) if self.is_local else await self._k8s_get_job_status(job_id)
        return _job_status_from_dict(d)

    async def list_jobs(self, filters: dict | None = None) -> list[JobStatus]:
        items = await self._local_list_jobs(filters) if self.is_local else await self._k8s_list_jobs(filters)
        return [_job_status_from_dict(d) for d in items]

    async def get_job_logs(self, job_id: str) -> str:
        if self.is_local:
            return f"[local mode] No logs available for job {job_id}"
        return await self._k8s_get_job_logs(job_id)

    async def get_cluster_status(self) -> ClusterStatus:
        d = self._local_cluster_status() if self.is_local else await self._k8s_get_cluster_status()
        return _cluster_status_from_dict(d)

    async def get_cluster_metrics(self) -> ClusterMetrics:
        d = self._local_cluster_metrics() if self.is_local else await self._k8s_get_cluster_metrics()
        return _cluster_metrics_from_dict(d)

    async def get_cluster_detail(self) -> ClusterDetail:
        """Cluster name/status/node-count + per-pool detail for the stack view.

        Owns the GKE cluster read that previously lived in
        stack_deployment.get_cluster_status (Phase 9 / Stage 3b). Status fields
        use the uppercase enum-name mapping with an ``UNKNOWN`` default to
        preserve the stack view's existing contract (distinct from the lowercased
        controller_status that get_cluster_status reports).
        """
        await self.load_cluster_config()
        cfg = self._cluster_config or {}
        cluster_name = _resolve_cfg(cfg, "gke_cluster_name", "GKE_CLUSTER_NAME")
        project_id = _resolve_cfg(cfg, "gcp_project_id", "GCP_PROJECT_ID")
        region = _resolve_cfg(cfg, "gcp_region", "GCP_REGION", default="us-central1")

        gke_client = self._get_gke_client()
        cluster = gke_client.get_cluster(name=f"projects/{project_id}/locations/{region}/clusters/{cluster_name}")

        node_pools = [
            NodePoolStatus(
                name=pool.name,
                machine_type=pool.config.machine_type,
                min_nodes=pool.autoscaling.min_node_count,
                max_nodes=pool.autoscaling.max_node_count,
                current_nodes=pool.initial_node_count,
                spot=pool.config.spot,
                status=self._GKE_STATUS_MAP.get(pool.status, "UNKNOWN"),
            )
            for pool in cluster.node_pools
        ]

        return ClusterDetail(
            name=cluster.name,
            status=self._GKE_STATUS_MAP.get(cluster.status, "UNKNOWN"),
            node_count=cluster.current_node_count,
            node_pools=node_pools,
        )

    # -- Cluster lifecycle management (orphan scan / recovery / teardown) ------

    async def list_cluster_names(self, project_id: str, location: str) -> list[str]:
        client = self._get_gke_client()
        response = client.list_clusters(parent=f"projects/{project_id}/locations/{location}")
        return [c.name for c in (response.clusters or [])]

    async def probe_cluster(self, project_id: str, location: str, cluster_name: str) -> ClusterProbe:
        client = self._get_gke_client()
        cluster_path = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"
        try:
            cluster = client.get_cluster(name=cluster_path)
        except Exception as exc:
            logger.info("GKE cluster %s not reachable: %s", cluster_name, exc)
            return ClusterProbe(state="NOT_FOUND")
        endpoint = getattr(cluster, "endpoint", "") or None
        ca_cert = None
        master_auth = getattr(cluster, "master_auth", None)
        if master_auth:
            ca_cert = getattr(master_auth, "cluster_ca_certificate", "") or None
        return ClusterProbe(
            state=self._GKE_STATUS_MAP.get(cluster.status, "UNKNOWN"),
            endpoint=endpoint,
            ca_cert=ca_cert,
        )

    async def delete_cluster(self, project_id: str, location: str, cluster_name: str) -> None:
        client = self._get_gke_client()
        client.delete_cluster(name=f"projects/{project_id}/locations/{location}/clusters/{cluster_name}")

    def _cost_estimate_dict(self, job_spec: dict) -> dict:
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

    async def get_cost_estimate(self, job_spec: dict) -> CostEstimate:
        return CostEstimate(**self._cost_estimate_dict(job_spec))

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

    async def get_job_progress(self, job_id: str) -> JobProgress:
        d = await self._local_get_job_progress(job_id) if self.is_local else await self._k8s_get_job_progress(job_id)
        return _job_progress_from_dict(d)

    async def get_connection_command(self, job_id: str) -> str:
        namespace = "bioaf-pipelines"
        return f"kubectl exec -it -n {namespace} job/{job_id} -- /bin/bash"

    def connection_setup_guide(self) -> str:
        from app.adapters.kubernetes.connection import KUBECTL_SETUP_GUIDE

        return KUBECTL_SETUP_GUIDE

    # -- Local mode implementations --

    async def _local_submit_job(self, job_spec: dict) -> dict:
        job_id = f"local-{uuid.uuid4().hex[:12]}"
        logger.info("Local mode: submitted job %s", job_id)
        cost_estimate = self._cost_estimate_dict(job_spec)
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

    async def _ensure_cluster_config_fresh(self) -> None:
        """Reload cluster config from platform_config when possible.

        Called from async public entry points before they invoke sync K8s
        helpers (which use the sync _get_api_client and don't reload config
        themselves). No-op when _session_factory is missing (test contexts
        where the caller has explicitly seeded _cluster_config) so seeded
        values aren't clobbered.
        """
        if self._session_factory is None:
            return
        await self.load_cluster_config(force=True)

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

    def _get_k8s_batch_client(self):
        """Get a Kubernetes BatchV1Api client. Tests mock this method."""
        return self._gke.batch_v1()

    def _get_k8s_rbac_client(self):
        """Get a Kubernetes RbacAuthorizationV1Api client. Tests mock this method."""
        return self._gke.rbac_v1()

    def _get_k8s_apps_client(self):
        """Get a Kubernetes AppsV1Api client. Tests mock this method."""
        return self._gke.apps_v1()

    async def ensure_cluster_autoscaler(
        self,
        *,
        role_arn: str,
        cluster_name: str,
        region: str,
        image: str | None = None,
    ) -> None:
        """Install/refresh the in-cluster Cluster Autoscaler (EKS only).

        EKS managed node groups do not pod-autoscale natively (GKE does), so on
        AWS a launched pipeline/notebook pod that targets a scaled-to-zero pool
        sits Pending forever. This deploys the CA workload (kube-system SA + RBAC
        + Deployment) through the cluster connection -- the same way
        ``ensure_pipeline_namespace`` creates the pipeline namespace/SA -- so it
        is fully code-driven, no manual kubectl. The SA is annotated via the
        PodIdentity seam with the IRSA ``role_arn`` the terraform module created;
        the Deployment's auto-discovery flag matches the ASG tags that module
        stamps. Idempotent: re-running create-or-patches each object, so a
        redeploy or a repair pass converges.

        This is never called on GCP (the deploy flow gates it on cloud_provider);
        it lives on the shared K8s provider only because it needs the cluster
        connection. ``role_arn``/``cluster_name``/``region`` come from
        platform_config (written by the compute deploy).
        """
        from kubernetes.client.rest import ApiException

        # The cluster was (re)deployed moments ago; force a fresh read of the
        # endpoint/CA/token from platform_config before building the client (the
        # cached config from startup predates this cluster). invalidate_client_on
        # _force=True drops any stale client so it rebuilds against the new cfg.
        await self._gke.load_cluster_config(force=True)

        sa_annotations = self._pod_identity.pod_identity_annotations(role_arn)
        m = build_cluster_autoscaler_manifests(
            role_arn=role_arn,
            cluster_name=cluster_name,
            region=region,
            sa_annotations=sa_annotations,
            image=image,
        )

        core_v1 = self._get_k8s_core_client()
        rbac_v1 = self._get_k8s_rbac_client()
        apps_v1 = self._get_k8s_apps_client()
        ns = "kube-system"

        def _apply(create, kind: str, replace=None) -> None:
            try:
                create()
                logger.info("Created cluster-autoscaler %s", kind)
            except ApiException as e:
                if e.status == 409:
                    # Already present (redeploy / repair). Update the mutable
                    # objects (SA annotation, Deployment image/args/role) so the
                    # refresh actually takes; leave the static RBAC as-is.
                    if replace is not None:
                        replace()
                        logger.info("Updated cluster-autoscaler %s", kind)
                    else:
                        logger.info("cluster-autoscaler %s already exists", kind)
                else:
                    raise

        _apply(
            lambda: core_v1.create_namespaced_service_account(namespace=ns, body=m["service_account"]),
            "service account",
            replace=lambda: core_v1.patch_namespaced_service_account(
                name="cluster-autoscaler", namespace=ns, body=m["service_account"]
            ),
        )
        _apply(lambda: rbac_v1.create_cluster_role(body=m["cluster_role"]), "cluster role")
        _apply(lambda: rbac_v1.create_cluster_role_binding(body=m["cluster_role_binding"]), "cluster role binding")
        _apply(lambda: rbac_v1.create_namespaced_role(namespace=ns, body=m["role"]), "role")
        _apply(
            lambda: rbac_v1.create_namespaced_role_binding(namespace=ns, body=m["role_binding"]),
            "role binding",
        )
        _apply(
            lambda: apps_v1.create_namespaced_deployment(namespace=ns, body=m["deployment"]),
            "deployment",
            replace=lambda: apps_v1.patch_namespaced_deployment(
                name="cluster-autoscaler", namespace=ns, body=m["deployment"]
            ),
        )
        logger.info("Cluster autoscaler ensured for cluster %s", cluster_name)

    _autoscaler_ready = False

    async def _ensure_autoscaler_if_aws(self) -> None:
        """Lazily install the Cluster Autoscaler on first pipeline launch (EKS).

        Self-healing entry point: an EKS cluster deployed before this feature (or
        one whose deploy-time install failed) gets the CA the next time a pipeline
        runs, with no teardown/redeploy or manual kubectl. Gated by the presence
        of ``cluster_autoscaler_role_arn`` in cluster config, which only AWS sets
        (GKE autoscales natively), so this is a no-op on GCP. Cached per process
        via ``_autoscaler_ready`` and idempotent regardless. Best-effort: a
        failure must not block the job submit (the pod would just stay Pending, as
        it does today), so it is logged, not raised; a retry happens on the next
        launch since the ready flag is only set on success.
        """
        if self._autoscaler_ready:
            return
        cfg = self._cluster_config or {}
        role_arn = cfg.get("cluster_autoscaler_role_arn")
        if not role_arn or role_arn == "null":
            return  # GCP / not an autoscaler-capable install: nothing to do.

        region = cfg.get("aws_region")
        image = cfg.get("cluster_autoscaler_image")
        try:
            await self.ensure_cluster_autoscaler(
                role_arn=role_arn,
                cluster_name=(cfg.get("gke_cluster_name") or ""),
                region=(region if region and region != "null" else ""),
                image=(image if image and image != "null" else None),
            )
            self._autoscaler_ready = True
        except Exception:
            logger.exception("Lazy cluster-autoscaler install failed; pipeline pods may stay Pending")

    _namespace_ready = False

    async def ensure_pipeline_namespace(self, namespace: str = "bioaf-pipelines", gcp_sa_email: str = "") -> None:
        """Ensure the pipeline namespace, service account, and role binding exist.

        When gcp_sa_email is provided, the KSA carries the
        iam.gke.io/gcp-service-account annotation so pods get GCP credentials
        via Workload Identity. Without it, Nextflow pods running on the
        bioaf-pipelines node pool (workload_metadata=GKE_METADATA) have no
        GCP identity and GCS reads fail with 'storage.objects.get denied'.
        """
        from kubernetes.client.rest import ApiException

        core_v1 = self._get_k8s_core_client()
        rbac_v1 = self._get_k8s_rbac_client()

        # Check if namespace already exists
        try:
            core_v1.read_namespace(name=namespace)
            logger.info("Namespace %s already exists, skipping setup", namespace)
            if gcp_sa_email:
                self._patch_sa_annotation(core_v1, namespace, gcp_sa_email)
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

        # KSA annotations binding the pod to a cloud IAM identity (GKE Workload
        # Identity today); empty when no SA email is configured.
        sa_annotations = self._pod_identity.pod_identity_annotations(gcp_sa_email)

        # Create service account
        core_v1.create_namespaced_service_account(
            namespace=namespace,
            body=client.V1ServiceAccount(
                metadata=client.V1ObjectMeta(
                    name="bioaf-pipeline-runner",
                    labels={"bioaf.io/managed": "true"},
                    annotations=sa_annotations or None,
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

    def _patch_sa_annotation(self, core_v1, namespace: str, gcp_sa_email: str) -> None:
        """Ensure the pipeline-runner KSA carries the pod-identity binding.

        Used on the upgrade path where the namespace was created before pod
        identity was wired (no binding annotation on the existing KSA).
        """
        desired = self._pod_identity.pod_identity_annotations(gcp_sa_email)
        try:
            sa = core_v1.read_namespaced_service_account(name="bioaf-pipeline-runner", namespace=namespace)
            current = sa.metadata.annotations or {}
            if desired and any(current.get(k) != v for k, v in desired.items()):
                core_v1.patch_namespaced_service_account(
                    name="bioaf-pipeline-runner",
                    namespace=namespace,
                    body={"metadata": {"annotations": desired}},
                )
                logger.info("Patched pod-identity annotation on bioaf-pipeline-runner")
        except Exception:
            logger.exception("Failed to patch pod-identity annotation on bioaf-pipeline-runner")

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
        try:
            credentials = _load_gcp_credentials(cfg)
        except Exception:
            return container_v1.ClusterManagerClient()
        return container_v1.ClusterManagerClient(credentials=credentials)

    NEXTFLOW_IMAGE = "nextflow/nextflow:25.10.4"

    @staticmethod
    def _build_nextflow_command(
        job_spec: dict,
        report_gcs_path: str = "",
        trace_gcs_path: str = "",
        igenomes_ignore: bool = False,
    ) -> list[str]:
        """Build a Nextflow run command from the job spec.

        Translates pipeline_source, pipeline_version, parameters, and
        sample_sheet into a shell command that nextflow can execute.

        When ``igenomes_ignore`` is set (AWS runs), append ``--igenomes_ignore
        true`` unless the run already sets it. nf-core pipelines default
        ``igenomes_base`` to ``s3://ngi-igenomes/igenomes/`` (a public AWS bucket),
        and schema validation reads that path. On AWS the pipeline pod's IRSA
        creds are scoped to ``bioaf-*``, so the SIGNED read of ngi-igenomes 403s;
        on GCP the same read goes out anonymous and succeeds. Defaulting igenomes
        off on AWS avoids the 403 for pipelines that do not need iGenomes (bioAF
        manages references via its own bucket). GCP passes ``False`` -> unchanged.
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

        # outdir is guaranteed durable by _ensure_outdir before this runs (a gs://
        # results path, or the launch already failed closed), so it is never
        # defaulted to a pod-local path that pod cleanup would destroy.

        # Default iGenomes off on AWS (signed IRSA reads 403 on the public
        # ngi-igenomes bucket). Skip when the run sets igenomes_ignore explicitly
        # so an operator override below still wins.
        if igenomes_ignore and "igenomes_ignore" not in parameters:
            parts.extend(["--igenomes_ignore", "true"])

        # Strip bioAF-internal config knobs that are not Nextflow parameters. "accessions" is the
        # carrier for fetchngs's ids file (materialized into --input via the sample sheet), not a
        # nextflow flag, so it must never be emitted as a bogus --accessions argument.
        internal_keys = {"fusion_enabled", "accessions"}

        for key, value in sorted(parameters.items()):
            if key in internal_keys:
                continue
            parts.extend([f"--{key}", str(value)])

        return ["/bin/sh", "-c", " ".join(parts)]

    def _ensure_outdir(self, job_spec: dict) -> dict:
        """Return ``job_spec`` with a durable outdir guaranteed.

        Keeps an explicitly-supplied outdir. Otherwise resolves the results bucket
        (explicit results_bucket_name, else derived from raw_bucket_name) and sets
        outdir to a durable gs:// path. FAILS CLOSED when no results bucket can be
        resolved, rather than letting outdir fall back to a pod-local path that the
        Job's ttlSecondsAfterFinished destroys an hour later (the (!)E silent-loss
        path). Returns a new dict; the input job_spec is not mutated.
        """
        params = job_spec.get("parameters", {})
        if "outdir" in params:
            return job_spec
        results_bucket = _resolve_results_bucket(self._cluster_config or {})
        if not results_bucket:
            raise RuntimeError(
                "Cannot launch pipeline: no results bucket is configured (set "
                "results_bucket_name in platform_config, or a bioaf-raw- prefixed "
                "raw_bucket_name) and no explicit outdir was provided. Refusing to "
                "write outputs to a pod-local path that is destroyed at pod cleanup."
            )
        experiment_id = job_spec.get("experiment_id", "unknown")
        run_id = job_spec.get("run_id", 0)
        # Backend-neutral outdir URI (gs:// on GCS, s3:// on S3) via the storage
        # seam, instead of a hardcoded gs:// literal.
        from app.adapters.registry import get_storage_adapter

        outdir = get_storage_adapter().build_uri(results_bucket, f"experiments/{experiment_id}/pipeline-runs/{run_id}")
        return {**job_spec, "parameters": {**params, "outdir": outdir}}

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
        ignore_igenomes_base: bool = False,
        pipeline_disk_gb: int | None = None,
    ) -> str:
        """Build a nextflow.config for K8s executor mode.

        Each Nextflow process runs as its own K8s pod. The config ensures
        pods use the right service account, have GCS credentials when
        available, and share a GCS-backed work directory so the head pod
        and process pods can exchange command scripts and data.

        When ``ignore_igenomes_base`` is set (AWS runs), tell nf-schema to skip
        validating the ``igenomes_base`` parameter. nf-core defaults it to the
        public ``s3://ngi-igenomes/igenomes/`` bucket and nf-schema's
        ``directory-path`` format check does a live S3 access to validate it; on
        AWS the pipeline pod's IRSA creds (scoped to ``bioaf-*``) sign that read
        and get 403, so validation fails before the pipeline starts. (On GCP the
        same read is anonymous and succeeds, so GCP omits this.) ``ignoreParams``
        is the nf-schema 2.x user list and merges with the pipeline's
        ``defaultIgnoreParams``, so it does not clobber the pipeline's own.
        """
        lines = [
            "process.executor = 'k8s'",
            f"k8s.namespace = '{namespace}'",
            "k8s.serviceAccount = 'bioaf-pipeline-runner'",
        ]

        # Skip nf-schema's live S3 validation of the public igenomes_base default
        # (IRSA-signed reads 403 on ngi-igenomes). AWS-gated; GCP never sets this.
        if ignore_igenomes_base:
            lines.append("validation.ignoreParams = ['igenomes_base']")

        # Scratch workDir so head and process pods share files. The storage
        # backend supplies its Nextflow workDir directives (ScratchWorkDir seam):
        # GCS overlays the gs:// workDir with Wave+Fusion as a local filesystem.
        if gcs_work_dir:
            from app.adapters.registry import get_storage_adapter

            lines.extend(get_storage_adapter().nextflow_scratch_directives(gcs_work_dir))

        # Resource limits and preemption-aware retry strategy (ADR-042).
        # Prevents retry escalation from requesting more than a single
        # node can provide, and retries Spot preemptions without
        # escalating resources.
        machine = pipeline_machine_type or "n2-highmem-16"
        cpus, mem_gb = KubernetesComputeProvider._MACHINE_ALLOCATABLE.get(machine, (14, 110))
        lines.append(f"process.resourceLimits = [cpus: {cpus}, memory: '{mem_gb}.GB']")

        # Ephemeral storage, which nothing declared until run 43 was evicted for want of it:
        # "The node was low on resource: ephemeral-storage ... Container was using 80520660Ki,
        # request is 0". Nextflow's `disk` directive is what the k8s executor turns into an
        # ephemeral-storage request, and with no request the scheduler packs two genome-scale steps
        # onto one node and kubelet kills the larger of them, hours in.
        #
        # The node's disk carries the OS, the container images and kubelet's ~10 GB eviction
        # threshold as well as the work dir, so the request is sized against what is left rather
        # than the raw disk. One genome-scale step per node is deliberate: it is what the memory
        # limit above already implies on these machine types, and over-packing is what broke.
        #
        # It ESCALATES with the attempt. Nextflow cannot see a pod's termination reason, so an
        # eviction and a Spot preemption are both just exit 137 and cannot be told apart -- which is
        # why retrying unchanged was wrong. Asking for more room each time resolves the eviction
        # case without needing to identify it, and costs a preempted task nothing. Capped at the
        # usable disk, because a request above allocatable schedules nowhere at all.
        usable_disk_gb = max(20, (pipeline_disk_gb or 100) - 20)
        first_attempt_gb = max(10, usable_disk_gb // 2)
        lines.append(f'process.disk = {{ "${{Math.min({usable_disk_gb}, {first_attempt_gb} * task.attempt)}}.GB" }}')

        lines.append("process.maxRetries = 3")
        # Exit 143 (SIGTERM) and 137 (SIGKILL) from Spot preemption: retry
        # without escalating. Other failures: escalate then finish.
        lines.append(
            "process.errorStrategy = { "
            "task.exitStatus in [143, 137, 247] "
            "? (task.attempt <= 3 ? 'retry' : 'finish') "
            ": (task.attempt <= 2 ? 'retry' : 'finish') }"
        )

        # Build k8s.pod directives for task pod placement, secrets, and env.
        #
        # Placement: the bioaf-pipelines pool is tainted (bioaf.io/pool=pipelines:NoSchedule) so
        # GKE-managed system addons can never pin an expensive node and block scale-to-zero. Task
        # pods therefore MUST carry the matching toleration to schedule there, plus a nodeSelector
        # pinning them to the pool. Nextflow 25.10 supports the `toleration` pod option (an earlier
        # assumption that it did not is why the pool used to be left untainted).
        #
        # safe-to-evict=false keeps long-running steps (STAR_GENOMEGENERATE, alignment, ...) from
        # being killed when the autoscaler decides their node is "underutilized".
        pod_directives: list[str] = [
            "[annotation: 'cluster-autoscaler.kubernetes.io/safe-to-evict', value: 'false']",
            "[nodeSelector: 'bioaf.io/pool=pipelines']",
            "[toleration: [key: 'bioaf.io/pool', operator: 'Equal', value: 'pipelines', effect: 'NoSchedule']]",
        ]
        if has_gcs_secret:
            pod_directives.append("[secret: 'bioaf-gcs-sa-key', mountPath: '/secrets/gcp']")
            pod_directives.append("[env: 'GOOGLE_APPLICATION_CREDENTIALS', value: '/secrets/gcp/key.json']")

        lines.append("k8s.pod = [" + ", ".join(pod_directives) + "]")

        # Docker is the default container engine for nf-core
        lines.append("docker.enabled = true")

        # MultiQC 1.20+ no longer writes multiqc_plots/png/ by default. The
        # bioAF QC dashboard collects PNGs from that directory, so force
        # MultiQC to export them by passing --export. Scope this to the MULTIQC
        # process selector so only the MultiQC task picks it up.
        #
        # ext.args is set to a plain value, NOT a closure that reads
        # task.ext.args. A closure like `{ (task.ext.args ?: '') + ' --export' }`
        # is self-referential: at resolution time task.ext.args is this very
        # closure, so it recurses forever and Nextflow aborts MULTIQC with
        # java.lang.StackOverflowError before the process runs. Nextflow has no
        # native append for ext.args (a later assignment fully replaces the
        # earlier one), so we override outright. This drops nf-core's default
        # MULTIQC ext.args (the --title from params.multiqc_title); the QC
        # dashboard does not depend on the report title.
        lines.append("process { withName: 'MULTIQC' { ext.args = '--export' } }")

        return "\n".join(lines)

    async def _read_gcp_credentials(self) -> tuple[str, str]:
        """Read gcp_credential_source and gcp_service_account_key fresh from platform_config.

        Cluster endpoint/CA can be cached in _cluster_config because they rarely
        change, but credentials need a per-launch read so that keys saved or
        rotated through the Settings UI take effect without a backend restart.
        """
        from app.platform.platform_config_service import PlatformConfigService

        if not self._session_factory:
            return "vm_default", ""

        async with self._session_factory() as session:
            rows = await PlatformConfigService.get_many(session, ["gcp_credential_source", "gcp_service_account_key"])

        return rows.get("gcp_credential_source") or "vm_default", rows.get("gcp_service_account_key", "")

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

        Returns the K8s resource name so callers can mount it.
        """
        import base64

        from kubernetes.client.rest import ApiException

        core_client = self._get_k8s_core_client()
        resource_name = f"bioaf-ssh-key-{run_id}"

        try:
            core_client.read_namespaced_secret(name=resource_name, namespace=namespace)
            return resource_name
        except ApiException as exc:
            if exc.status != 404:
                logger.exception("Error checking SSH resource")
                raise

        core_client.create_namespaced_secret(
            namespace=namespace,
            body={
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": resource_name, "labels": {"bioaf.io/managed": "true"}},
                "type": "Opaque",
                "data": {"id_rsa": base64.b64encode(ssh_private_key.encode()).decode()},
            },
        )
        logger.info("Created SSH resource in namespace %s for run %s", namespace, run_id)
        return resource_name

    async def _k8s_submit_job(self, job_spec: dict) -> dict:
        """Submit a real Kubernetes Job to the GKE cluster."""
        # Force-reload cluster config. The sync _get_api_client() used by
        # downstream sync K8s helpers does not itself reload config, so
        # without this call a backend that started before compute deploy
        # will fail with "No GKE cluster endpoint in platform_config"
        # even after deploy finishes.
        await self._ensure_cluster_config_fresh()

        run_id = job_spec.get("run_id", 0)
        pipeline_name = job_spec.get("pipeline_name", "unknown")
        namespace = job_spec.get("namespace", "bioaf-pipelines")
        container_image = job_spec.get("container_image", "alpine:3.19")
        command = job_spec.get("command", [])
        stage_commands = job_spec.get("stage_commands", [])
        pipeline_source = job_spec.get("pipeline_source", "")
        sample_sheet = job_spec.get("sample_sheet", "")

        # Ensure namespace, service account, and role binding exist on first use.
        # Pass the pipeline-runner cloud identity so the KSA gets the right
        # pod-identity annotation (the PodIdentity seam maps it): on GCP the
        # bioaf-pipeline-runner GSA email -> iam.gke.io/gcp-service-account; on AWS
        # the IRSA role ARN -> eks.amazonaws.com/role-arn.
        cfg = self._cluster_config or {}
        project_id = cfg.get("gcp_project_id", "")
        runner_role_arn = cfg.get("pipeline_runner_role_arn", "")
        if runner_role_arn and runner_role_arn != "null":
            pipeline_runner_identity = runner_role_arn
        elif project_id:
            pipeline_runner_identity = f"bioaf-pipeline-runner@{project_id}.iam.gserviceaccount.com"
        else:
            pipeline_runner_identity = ""
        if not self._namespace_ready:
            await self.ensure_pipeline_namespace(namespace, gcp_sa_email=pipeline_runner_identity)

        # On EKS, make sure the Cluster Autoscaler is running before the head pod
        # goes Pending -- otherwise its scale-to-zero pool never gets a node and
        # the run hangs with empty logs. No-op on GCP (native autoscaling).
        await self._ensure_autoscaler_if_aws()

        # Ensure GCS credentials secret exists for bucket access. Read the
        # SA key fresh from platform_config so a key saved or rotated through
        # the Settings UI takes effect without a backend restart.
        credential_source, sa_key = await self._read_gcp_credentials()
        has_gcs_secret = self._ensure_gcs_secret(namespace, credential_source, sa_key)

        # job_name embeds an epoch-second suffix so the K8s Job name and the
        # derived GCS paths (-with-report, -with-trace, persisted log) are
        # unique even when run_id is recycled (e.g., after a DB sequence
        # reset). Reads use pipeline_runs.k8s_job_name as the authoritative
        # key, so the suffix flows through transparently.
        job_name = f"bioaf-pipeline-{run_id}-{int(time.time())}"

        # Auto-build Nextflow command when pipeline_source is set and no
        # explicit command was provided
        if pipeline_source and not command:
            container_image = self.NEXTFLOW_IMAGE

            from app.adapters.registry import get_storage_adapter

            nf_cfg = self._cluster_config or {}
            raw_bucket = nf_cfg.get("raw_bucket_name", "")

            # Write the Nextflow HTML report and execution trace directly to the
            # object store (gs:// on GCS, s3:// on S3, via the storage seam) so they
            # persist after the head pod is cleaned up.
            _adapter = get_storage_adapter()
            report_gcs_path = (
                _adapter.build_uri(raw_bucket, f"nextflow-reports/{job_name}/report.html") if raw_bucket else ""
            )
            trace_gcs_path = (
                _adapter.build_uri(raw_bucket, f"nextflow-traces/{job_name}/trace.tsv") if raw_bucket else ""
            )

            # Set --outdir to a durable results-bucket path so pipeline outputs
            # persist after pod cleanup. The path mirrors the prefix that
            # _gcs_collect_outputs and _extract_metrics use to find outputs.
            # _ensure_outdir resolves the results bucket explicitly and fails closed
            # if none is configured (rather than silently using a pod-local path).
            job_spec = self._ensure_outdir(job_spec)

            command = self._build_nextflow_command(
                job_spec,
                report_gcs_path=report_gcs_path,
                trace_gcs_path=trace_gcs_path,
                # AWS runs carry an IRSA runner role; default iGenomes off there
                # so schema validation does not 403 on the public ngi-igenomes
                # bucket. GCP has no runner role arn -> False -> unchanged.
                igenomes_ignore=bool(runner_role_arn and runner_role_arn != "null"),
            )

        # Build init containers for input staging. The stage container runs the
        # storage backend's CLI image (CopyStager seam); GCS -> google/cloud-sdk:slim.
        init_containers = []
        if stage_commands:
            from app.adapters.registry import get_storage_adapter

            stage_script = " && ".join(stage_commands)
            init_containers.append(
                {
                    "name": "stage-inputs",
                    "image": get_storage_adapter().staging_image(),
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
            from app.adapters.registry import get_storage_adapter

            nf_cfg = self._cluster_config or {}
            raw_bucket = nf_cfg.get("raw_bucket_name", "")
            # Backend-neutral workDir URI (gs:// on GCS, s3:// on S3) via the
            # storage seam, instead of a hardcoded gs:// literal.
            scratch_work_dir = get_storage_adapter().build_uri(raw_bucket, "nextflow-work") if raw_bucket else None
            pipeline_machine = nf_cfg.get("k8s_pipeline_machine_type")
            pipeline_disk_raw = (nf_cfg.get("k8s_pipeline_disk_size_gb") or "").strip()
            pipeline_disk = int(pipeline_disk_raw) if pipeline_disk_raw.isdigit() else None
            # AWS runs use IRSA creds scoped to bioaf-*, so skip nf-schema's live
            # validation of the public igenomes_base default (it 403s). GCP omits.
            nf_config = self._build_nextflow_k8s_config(
                namespace,
                has_gcs_secret,
                scratch_work_dir,
                pipeline_machine,
                ignore_igenomes_base=bool(runner_role_arn and runner_role_arn != "null"),
                pipeline_disk_gb=pipeline_disk,
            )
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
                    "metadata": {
                        # Pin the head pod to its node. Without this, the
                        # cluster autoscaler treats Nextflow's coordinator pod
                        # as "movable" (it requests no resources) and scales
                        # down its node mid-pipeline, killing the workflow
                        # and any task pods it's coordinating.
                        "annotations": {
                            "cluster-autoscaler.kubernetes.io/safe-to-evict": "false",
                        },
                    },
                    "spec": {
                        # Head pod runs on the dedicated on-demand pipeline-head
                        # pool, NOT the Spot pipelines pool. Spot preemption of
                        # the head pod kills the entire pipeline; on-demand keeps
                        # the orchestrator alive while task pods (Spot, retried
                        # by Nextflow's errorStrategy on exit 143/137/247) stay
                        # cheap. This head pool is tainted bioaf.io/pool=pipeline-head;
                        # task pods tolerate only the pipelines taint, so they never land here.
                        "nodeSelector": {"bioaf.io/pool": "pipeline-head"},
                        "tolerations": [
                            {
                                "key": "bioaf.io/pool",
                                "value": "pipeline-head",
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
                    },
                },
            },
        }

        if init_containers:
            job_manifest["spec"]["template"]["spec"]["initContainers"] = init_containers  # type: ignore[index]

        batch_client = self._get_k8s_batch_client()
        batch_client.create_namespaced_job(namespace=namespace, body=job_manifest)

        cost_estimate = self._cost_estimate_dict(job_spec)

        return {
            "job_id": job_name,
            "namespace": namespace,
            "status": "queued",
            "estimated_cost": cost_estimate,
        }

    async def _k8s_cancel_job(self, job_id: str) -> dict:
        """Delete a Kubernetes Job with background propagation."""
        await self._ensure_cluster_config_fresh()
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
        await self._ensure_cluster_config_fresh()
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
        await self._ensure_cluster_config_fresh()
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
        await self._ensure_cluster_config_fresh()
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
                return _pod_log_to_text(logs)
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
        await self._ensure_cluster_config_fresh()
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
            logs = _pod_log_to_text(
                core_client.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=namespace,
                    container="pipeline",
                )
            )
        except Exception:
            logger.warning("Could not read logs from %s for persistence", pod_name)
            return False

        if not logs:
            return False

        log_path = f"nextflow-traces/{job_id}/pipeline.log"
        try:
            from google.cloud import storage as gcs_storage

            try:
                credentials = _load_gcp_credentials(cfg)
                storage_client = gcs_storage.Client(credentials=credentials)
            except Exception:
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

        log_path = f"nextflow-traces/{job_id}/pipeline.log"

        try:
            from google.cloud import storage as gcs_storage

            try:
                credentials = _load_gcp_credentials(cfg)
                storage_client = gcs_storage.Client(credentials=credentials)
            except Exception:
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

        report_path = f"nextflow-reports/{job_id}/report.html"

        try:
            from google.cloud import storage as gcs_storage

            try:
                credentials = _load_gcp_credentials(cfg)
                storage_client = gcs_storage.Client(credentials=credentials)
            except Exception:
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

        try:
            import google.cloud.logging

            try:
                credentials = _load_gcp_credentials(cfg)
                log_client = google.cloud.logging.Client(project=project_id, credentials=credentials)
            except Exception:
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

        trace_path = f"nextflow-traces/{job_id}/trace.tsv"

        try:
            from google.cloud import storage as gcs_storage

            try:
                credentials = _load_gcp_credentials(cfg)
                storage_client = gcs_storage.Client(credentials=credentials)
            except Exception:
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
        """Parse Nextflow trace TSV content into normalized progress structure.

        Delegates to the shared ``app.pipeline.nextflow_trace`` parser (the
        single source of truth shared with the pipeline monitor).
        """
        return nextflow_trace.parse_trace_to_progress(content)

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
        cluster_name = _resolve_cfg(cfg, "gke_cluster_name", "GKE_CLUSTER_NAME")
        project_id = _resolve_cfg(cfg, "gcp_project_id", "GCP_PROJECT_ID")
        region = _resolve_cfg(cfg, "gcp_region", "GCP_REGION", default="us-central1")

        if not cluster_name or not project_id:
            raise RuntimeError(
                "GKE cluster identity not configured (gke_cluster_name / gcp_project_id missing). "
                "Re-run compute deploy to populate platform_config."
            )

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
        cluster_name = _resolve_cfg(cfg, "gke_cluster_name", "GKE_CLUSTER_NAME")
        project_id = _resolve_cfg(cfg, "gcp_project_id", "GCP_PROJECT_ID")
        region = _resolve_cfg(cfg, "gcp_region", "GCP_REGION", default="us-central1")

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
