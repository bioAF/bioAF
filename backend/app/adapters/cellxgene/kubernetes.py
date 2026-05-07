"""Kubernetes cellxgene adapter.

Always deploys real cellxgene pods to the GKE cluster. When running outside
the cluster (e.g., Docker Compose on a VM), the adapter builds a K8s client
from platform_config credentials (gke_cluster_endpoint, gke_cluster_ca_cert,
GCP service account key).
"""

import asyncio
import base64
import logging
import tempfile
import time
from datetime import datetime, timezone

from kubernetes import client, config

from app.adapters.base import CellxgeneProvider

logger = logging.getLogger("bioaf.adapters.cellxgene.k8s")


def _get_gcp_token(cfg: dict) -> str:
    """Mint a GCP access token via credential_injector.

    Routes through impersonated bootstrap credentials in vm_default mode
    or the stored JSON key in legacy service_account_key mode.
    """
    import google.auth.transport.requests

    from app.services import credential_injector

    credentials = credential_injector.load_gcp_credentials(cfg)
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


DEFAULT_CELLXGENE_NAMESPACE = "bioaf-cellxgene"


class KubernetesCellxgeneProvider(CellxgeneProvider):
    """Kubernetes cellxgene backend.

    Connects to the GKE cluster via incluster config (when running as a pod)
    or platform_config credentials (when running outside the cluster, e.g.
    Docker Compose on a GCP VM).
    """

    _TOKEN_TTL_SECONDS = 2700  # 45 minutes

    def __init__(self, session_factory=None):
        self._session_factory = session_factory
        self._api_client: client.ApiClient | None = None
        self._client_created_at: float = 0.0
        self._cluster_config: dict | None = None
        self._namespace_ready = False

    async def _read_platform_config(self, *keys: str) -> dict[str, str]:
        """Read values from platform_config."""
        if not self._session_factory:
            return {}

        from sqlalchemy import text as sa_text

        placeholders = ", ".join(f"'{k}'" for k in keys)
        async with self._session_factory() as session:
            result = await session.execute(
                sa_text(f"SELECT key, value FROM platform_config WHERE key IN ({placeholders})")
            )
            return {r[0]: r[1] for r in result.fetchall()}

    async def _resolve_image(self) -> str:
        """Read the cellxgene image URI from platform_config."""
        config = await self._read_platform_config("cellxgene_image")
        uri = config.get("cellxgene_image")
        if not uri or uri == "null":
            raise RuntimeError("Cellxgene image not built yet. Enable the cellxgene component to trigger a build.")
        return uri

    async def _ensure_gcp_secret(self, namespace: str) -> None:
        """Create or update a K8s Secret with the GCP service account key."""
        from kubernetes.client.rest import ApiException

        config = await self._read_platform_config("gcp_service_account_key")
        sa_key = config.get("gcp_service_account_key", "")
        if not sa_key or sa_key == "null":
            logger.warning("No GCP service account key in platform_config; skipping secret")
            return

        core_v1 = self._get_k8s_core_client()
        secret_name = "gcp-sa-key"

        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=secret_name,
                namespace=namespace,
                labels={"bioaf.io/managed": "true"},
            ),
            string_data={"key.json": sa_key},
        )

        try:
            core_v1.read_namespaced_secret(name=secret_name, namespace=namespace)
            core_v1.replace_namespaced_secret(name=secret_name, namespace=namespace, body=secret)
            logger.info("Updated GCP SA secret in %s", namespace)
        except ApiException as e:
            if e.status == 404:
                core_v1.create_namespaced_secret(namespace=namespace, body=secret)
                logger.info("Created GCP SA secret in %s", namespace)
            else:
                raise

    async def deploy(self, publication_id: int, gcs_uri: str, dataset_name: str) -> dict:
        await self._get_api_client_async()
        image = await self._resolve_image()

        namespace = DEFAULT_CELLXGENE_NAMESPACE
        await self.ensure_cellxgene_namespace(namespace)
        await self._ensure_gcp_secret(namespace)

        name = f"cellxgene-{publication_id}"
        local_path = "/data/dataset.h5ad"
        apps_v1 = self._get_k8s_apps_client()
        core_v1 = self._get_k8s_core_client()

        deployment = client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={
                    "bioaf.io/managed": "true",
                    "bioaf.io/publication": str(publication_id),
                },
            ),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(match_labels={"app": name}),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"app": name}),
                    spec=client.V1PodSpec(
                        service_account_name="bioaf-cellxgene-runner",
                        node_selector={"bioaf.io/pool": "interactive"},
                        tolerations=[
                            client.V1Toleration(
                                key="bioaf.io/pool",
                                value="interactive",
                                effect="NoSchedule",
                            )
                        ],
                        init_containers=[
                            client.V1Container(
                                name="gcs-download",
                                image="google/cloud-sdk:slim",
                                command=[
                                    "/bin/sh",
                                    "-c",
                                    f"gcloud auth activate-service-account --key-file=/gcp/key.json "
                                    f"&& gsutil cp '{gcs_uri}' {local_path}",
                                ],
                                volume_mounts=[
                                    client.V1VolumeMount(name="data", mount_path="/data"),
                                    client.V1VolumeMount(name="gcp-sa", mount_path="/gcp", read_only=True),
                                ],
                            )
                        ],
                        containers=[
                            client.V1Container(
                                name="cellxgene",
                                image=image,
                                args=["launch", "--host", "0.0.0.0", local_path],
                                ports=[client.V1ContainerPort(container_port=5005)],
                                volume_mounts=[
                                    client.V1VolumeMount(name="data", mount_path="/data", read_only=True),
                                ],
                                resources=client.V1ResourceRequirements(
                                    requests={"cpu": "1", "memory": "4Gi"},
                                    limits={"cpu": "2", "memory": "8Gi"},
                                ),
                            )
                        ],
                        volumes=[
                            client.V1Volume(
                                name="data",
                                empty_dir=client.V1EmptyDirVolumeSource(size_limit="20Gi"),
                            ),
                            client.V1Volume(
                                name="gcp-sa",
                                secret=client.V1SecretVolumeSource(secret_name="gcp-sa-key"),
                            ),
                        ],
                    ),
                ),
            ),
        )
        apps_v1.create_namespaced_deployment(namespace=namespace, body=deployment)
        logger.info("Created cellxgene deployment %s in %s", name, namespace)

        service = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={
                    "bioaf.io/managed": "true",
                    "bioaf.io/publication": str(publication_id),
                },
            ),
            spec=client.V1ServiceSpec(
                selector={"app": name},
                ports=[client.V1ServicePort(port=5005, target_port=5005)],
                type="LoadBalancer",
            ),
        )
        core_v1.create_namespaced_service(namespace=namespace, body=service)
        logger.info("Created cellxgene service %s in %s", name, namespace)

        # Poll for readiness in background
        asyncio.create_task(self._poll_deployment_ready(publication_id, name, namespace))

        return {
            "publication_id": publication_id,
            "pod_name": name,
            "namespace": namespace,
            "status": "starting",
            "access_url": None,
        }

    async def teardown(self, publication_id: int) -> dict:
        await self._get_api_client_async()

        name = f"cellxgene-{publication_id}"
        namespace = DEFAULT_CELLXGENE_NAMESPACE

        apps_v1 = self._get_k8s_apps_client()
        core_v1 = self._get_k8s_core_client()

        try:
            apps_v1.delete_namespaced_deployment(name=name, namespace=namespace)
            logger.info("Deleted cellxgene deployment %s", name)
        except Exception as e:
            logger.warning("Failed to delete cellxgene deployment %s: %s", name, e)

        try:
            core_v1.delete_namespaced_service(name=name, namespace=namespace)
            logger.info("Deleted cellxgene service %s", name)
        except Exception as e:
            logger.warning("Failed to delete cellxgene service %s: %s", name, e)

        return {
            "publication_id": publication_id,
            "status": "stopped",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_status(self, publication_id: int) -> dict:
        await self._get_api_client_async()

        name = f"cellxgene-{publication_id}"
        namespace = DEFAULT_CELLXGENE_NAMESPACE
        apps_v1 = self._get_k8s_apps_client()

        try:
            dep = apps_v1.read_namespaced_deployment_status(name=name, namespace=namespace)
            ready = dep.status.ready_replicas or 0
            status = "running" if ready >= 1 else "starting"
        except Exception:
            return {
                "publication_id": publication_id,
                "status": "unknown",
                "pod_name": name,
            }

        return {
            "publication_id": publication_id,
            "status": status,
            "pod_name": name,
            "namespace": namespace,
        }

    # -- Cluster config --

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
                    "  'gcp_service_account_email', 'gcp_bootstrap_sa_email',"
                    "  'gke_cluster_name', 'gcp_project_id', 'gcp_zone'"
                    ")"
                )
            )
            self._cluster_config = {r[0]: r[1] for r in result.fetchall()}

        if force:
            self._api_client = None

        return self._cluster_config

    def _build_out_of_cluster_client(self) -> client.ApiClient:
        """Build a K8s ApiClient using platform_config credentials."""
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
        configuration.api_key = {"authorization": f"Bearer {token}"}

        self._client_created_at = time.monotonic()
        return client.ApiClient(configuration)

    def _is_token_expired(self) -> bool:
        if self._client_created_at == 0.0:
            return False
        return (time.monotonic() - self._client_created_at) > self._TOKEN_TTL_SECONDS

    async def _get_api_client_async(self) -> client.ApiClient:
        """Get or create a K8s ApiClient, trying incluster first."""
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
        return client.CoreV1Api(api_client=self._get_api_client())

    def _get_k8s_apps_client(self):
        return client.AppsV1Api(api_client=self._get_api_client())

    def _get_k8s_rbac_client(self):
        return client.RbacAuthorizationV1Api(api_client=self._get_api_client())

    # -- Namespace setup --

    async def ensure_cellxgene_namespace(self, namespace: str = DEFAULT_CELLXGENE_NAMESPACE) -> None:
        """Ensure the cellxgene namespace and service account exist."""
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
                    name="bioaf-cellxgene-runner",
                    labels={"bioaf.io/managed": "true"},
                )
            ),
        )
        logger.info("Created service account bioaf-cellxgene-runner in %s", namespace)

        rbac_v1.create_namespaced_role_binding(
            namespace=namespace,
            body=client.V1RoleBinding(
                metadata=client.V1ObjectMeta(
                    name="bioaf-cellxgene-runner-binding",
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
                        name="bioaf-cellxgene-runner",
                        namespace=namespace,
                    )
                ],
            ),
        )
        logger.info("Created role binding in %s", namespace)
        self._namespace_ready = True

    # -- Background readiness polling --

    async def _poll_deployment_ready(self, publication_id: int, name: str, namespace: str) -> None:
        """Background: poll for deployment readiness and LB IP, then update the DB."""
        try:
            import httpx

            apps_v1 = self._get_k8s_apps_client()

            # Wait for deployment readiness (up to 5 minutes)
            deployment_ready = False
            for _ in range(60):
                try:
                    dep = apps_v1.read_namespaced_deployment_status(name=name, namespace=namespace)
                    if dep.status.ready_replicas and dep.status.ready_replicas >= 1:
                        deployment_ready = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(5)

            if not deployment_ready:
                logger.error("Cellxgene deployment %s not ready after 5 min", name)
                await self._update_publication_in_db(publication_id, "failed", None)
                return

            logger.info("Cellxgene deployment %s is ready, waiting for LB IP", name)

            # Wait for LoadBalancer external IP (up to 3 minutes)
            api_client = self._get_api_client()
            k8s_config = api_client.configuration
            svc_url = f"{k8s_config.host}/api/v1/namespaces/{namespace}/services/{name}"
            headers = {"Authorization": list(k8s_config.api_key.values())[0]}

            access_url = None
            for _ in range(36):
                try:
                    resp = httpx.get(
                        svc_url,
                        headers=headers,
                        verify=k8s_config.ssl_ca_cert or False,
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        ingress_list = resp.json().get("status", {}).get("loadBalancer", {}).get("ingress") or []
                        if ingress_list:
                            ext_ip = ingress_list[0].get("ip") or ingress_list[0].get("hostname")
                            access_url = f"http://{ext_ip}:5005"
                            logger.info("External URL for cellxgene %s: %s", publication_id, access_url)
                            break
                except Exception:
                    pass
                await asyncio.sleep(5)

            if not access_url:
                logger.warning("LoadBalancer IP not ready for cellxgene %s after 3 min", name)

            await self._update_publication_in_db(publication_id, "published", access_url)

        except Exception:
            logger.exception("Background poll failed for cellxgene %s", name)
            await self._update_publication_in_db(publication_id, "failed", None)

    async def _update_publication_in_db(self, publication_id: int, status: str, access_url: str | None) -> None:
        if not self._session_factory:
            logger.warning("No session_factory, cannot update publication %s in DB", publication_id)
            return

        try:
            async with self._session_factory() as db:
                from sqlalchemy import text

                now = datetime.now(timezone.utc)
                if status == "published":
                    await db.execute(
                        text(
                            "UPDATE cellxgene_publications "
                            "SET status = :status, published_at = :now, access_url = :url "
                            "WHERE id = :id"
                        ),
                        {"status": status, "now": now, "url": access_url, "id": publication_id},
                    )
                else:
                    await db.execute(
                        text("UPDATE cellxgene_publications SET status = :status WHERE id = :id"),
                        {"status": status, "id": publication_id},
                    )
                await db.commit()
                logger.info(
                    "Updated publication %s: status=%s access_url=%s",
                    publication_id,
                    status,
                    access_url,
                )
        except Exception:
            logger.exception("Failed to update publication %s in DB", publication_id)
