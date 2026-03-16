"""Kubernetes compute adapter.

Supports local/mock mode for development and real K8s API for production.
Mode is controlled by the BIOAF_COMPUTE_MODE environment variable.
"""

import logging
import os
import uuid
from datetime import datetime, timezone


from app.adapters.base import ComputeProvider

logger = logging.getLogger("bioaf.adapters.compute.k8s")


class KubernetesComputeProvider(ComputeProvider):
    """Kubernetes compute backend with local mode for development."""

    def __init__(self, session_factory=None):
        self._mode = os.environ.get("BIOAF_COMPUTE_MODE", "local")
        self._session_factory = session_factory

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
        input_count = len(job_spec.get("input_files", []))
        base_cost = 0.50
        estimated = base_cost + (input_count * 0.10)
        return {
            "estimated_cost_usd": round(estimated, 2),
            "confidence_low": round(estimated * 0.7, 2),
            "confidence_high": round(estimated * 1.5, 2),
            "currency": "USD",
            "basis": "input file count heuristic",
        }

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
                    "machine_type": "n2-highmem-8",
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

    def _get_k8s_core_client(self):
        """Get a Kubernetes CoreV1Api client. Tests mock this method."""
        from kubernetes import client, config

        config.load_incluster_config()
        return client.CoreV1Api()

    def _get_k8s_batch_client(self):
        """Get a Kubernetes BatchV1Api client. Tests mock this method."""
        from kubernetes import client, config

        config.load_incluster_config()
        return client.BatchV1Api()

    def _get_k8s_rbac_client(self):
        """Get a Kubernetes RbacAuthorizationV1Api client. Tests mock this method."""
        from kubernetes import client, config

        config.load_incluster_config()
        return client.RbacAuthorizationV1Api()

    _namespace_ready = False

    async def ensure_pipeline_namespace(self, namespace: str = "bioaf-pipelines") -> None:
        """Ensure the pipeline namespace, service account, and role binding exist."""
        from kubernetes import client
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
        """Get a GKE ClusterManager client. Tests mock this method."""
        from google.cloud import container_v1

        return container_v1.ClusterManagerClient()

    async def _k8s_submit_job(self, job_spec: dict) -> dict:
        """Submit a real Kubernetes Job to the GKE cluster."""
        run_id = job_spec.get("run_id", 0)
        pipeline_name = job_spec.get("pipeline_name", "unknown")
        namespace = job_spec.get("namespace", "bioaf-pipelines")
        container_image = job_spec.get("container_image", "alpine:3.19")
        command = job_spec.get("command", [])
        stage_commands = job_spec.get("stage_commands", [])
        _ = job_spec.get("input_files", [])  # reserved for future use

        job_name = f"bioaf-pipeline-{run_id}"

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

        # Build main container
        main_container = {
            "name": "pipeline",
            "image": container_image,
            "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        }
        if command:
            main_container["command"] = command

        # Build job manifest
        job_manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "namespace": namespace,
                "labels": {
                    "bioaf.io/pipeline-run": str(run_id),
                    "bioaf.io/pipeline": pipeline_name,
                    "bioaf.io/pool": "pipelines",
                },
            },
            "spec": {
                "backoffLimit": 0,
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
                        ],
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

        return {
            "job_id": job_id,
            "status": status,
            "pod_name": pod_name,
            "node_name": node_name,
        }

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
        """Retrieve logs from the pipeline pod."""
        core_client = self._get_k8s_core_client()
        namespace = "bioaf-pipelines"

        pod_list = core_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_id}",
        )
        if not pod_list.items:
            return f"No pods found for job {job_id}"

        pod_name = pod_list.items[0].metadata.name
        logs = core_client.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container="pipeline",
        )
        return logs

    async def _k8s_get_cluster_status(self) -> dict:
        """Query GKE API for real cluster status."""
        cluster_name = os.environ.get("GKE_CLUSTER_NAME", "")
        project_id = os.environ.get("GCP_PROJECT_ID", "")
        zone = os.environ.get("GCP_ZONE", "")

        client = self._get_gke_client()
        cluster = client.get_cluster(name=f"projects/{project_id}/locations/{zone}/clusters/{cluster_name}")

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

    async def _k8s_get_cluster_metrics(self) -> dict:
        """Query GKE API for cluster metrics."""
        cluster_name = os.environ.get("GKE_CLUSTER_NAME", "")
        project_id = os.environ.get("GCP_PROJECT_ID", "")
        zone = os.environ.get("GCP_ZONE", "")

        client = self._get_gke_client()
        cluster = client.get_cluster(name=f"projects/{project_id}/locations/{zone}/clusters/{cluster_name}")

        node_pools = []
        for pool in cluster.node_pools:
            node_pools.append(
                {
                    "name": pool.name,
                    "cpu_utilization_pct": 0.0,
                    "memory_utilization_pct": 0.0,
                    "cost_rate_hourly": 0.0,
                }
            )

        return {
            "cpu_utilization_pct": 0.0,
            "memory_utilization_pct": 0.0,
            "cost_burn_rate_hourly": 0.0,
            "node_pools": node_pools,
        }
