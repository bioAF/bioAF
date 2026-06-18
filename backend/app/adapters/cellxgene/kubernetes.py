"""Kubernetes cellxgene adapter.

Always deploys real cellxgene pods to the GKE cluster. When running outside
the cluster (e.g., Docker Compose on a VM), the adapter builds a K8s client
from platform_config credentials (gke_cluster_endpoint, gke_cluster_ca_cert,
GCP service account key).
"""

import asyncio
import logging
from datetime import datetime, timezone

from kubernetes import client

from app.adapters.base import CellxgeneProvider
from app.adapters.capabilities import ProviderCapabilities
from app.adapters.kubernetes.connection import GkeConnection
from app.adapters.models import CellxgeneInstance, ServiceState

logger = logging.getLogger("bioaf.adapters.cellxgene.k8s")


DEFAULT_CELLXGENE_NAMESPACE = "bioaf-cellxgene"


class KubernetesCellxgeneProvider(CellxgeneProvider):
    """Kubernetes cellxgene backend.

    Connects to the GKE cluster via incluster config (when running as a pod)
    or platform_config credentials (when running outside the cluster, e.g.
    Docker Compose on a GCP VM).
    """

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
        "cellxgene_runner_sa_email",
    ]

    def capabilities(self) -> ProviderCapabilities:
        """This backend provides cellxgene visualization instances."""
        return ProviderCapabilities(cellxgene=True)

    def __init__(self, session_factory=None):
        self._session_factory = session_factory
        # Cellxgene deploys are long-lived against a singleton provider, so the
        # connection must rebuild its client when the cluster identity changes
        # (fingerprint strategy), not only when the GCP token TTL elapses.
        # Otherwise a cluster teardown + redeploy leaves the adapter pointed at
        # the dead endpoint until the backend restarts.
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

    async def _read_platform_config(self, *keys: str) -> dict[str, str]:
        """Read values from platform_config, decrypting any sensitive keys."""
        if not self._session_factory:
            return {}

        from app.platform.platform_config_service import PlatformConfigService

        async with self._session_factory() as session:
            return await PlatformConfigService.get_many(session, list(keys))

    async def _resolve_image(self) -> str:
        """Read the cellxgene image URI from platform_config."""
        config = await self._read_platform_config("cellxgene_image")
        uri = config.get("cellxgene_image")
        if not uri or uri == "null":
            raise RuntimeError("Cellxgene image not built yet. Enable the cellxgene component to trigger a build.")
        return uri

    async def _ensure_gcp_secret(self, namespace: str) -> bool:
        """Create or update a K8s Secret with the GCP service account key.

        Returns True if the secret exists (created or already present). Returns
        False in ``vm_default`` mode, where no key is stored and the pod must
        authenticate via Workload Identity instead. The caller must not mount
        the ``gcp-sa-key`` secret when this returns False, or the pod will fail
        to start with ``secret "gcp-sa-key" not found``.
        """
        from kubernetes.client.rest import ApiException

        config = await self._read_platform_config("gcp_service_account_key")
        sa_key = config.get("gcp_service_account_key", "")
        if not sa_key or sa_key == "null":
            logger.info("No GCP service account key (vm_default mode); cellxgene pod will use Workload Identity")
            return False

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
        return True

    async def deploy(self, publication_id: int, storage_uri: str, dataset_name: str) -> dict:  # type: ignore[override]
        await self._get_api_client_async()
        image = await self._resolve_image()

        namespace = DEFAULT_CELLXGENE_NAMESPACE
        # Bind the runner KSA to the dedicated cellxgene-runner cloud identity
        # (created by the compute Terraform module: a Workload Identity binding +
        # bucket read on GCP; an IRSA role on AWS). The generic app identity has no
        # such binding for this KSA, so the init container's copy would 403 on the
        # dataset bucket without it.
        runner_identity = (self._cluster_config or {}).get("cellxgene_runner_sa_email", "") or ""
        if runner_identity == "null":
            runner_identity = ""
        await self.ensure_cellxgene_namespace(namespace, gcp_sa_email=runner_identity)
        has_gcs_key = await self._ensure_gcp_secret(namespace)

        name = f"cellxgene-{publication_id}"
        local_path = "/data/dataset.h5ad"
        apps_v1 = self._get_k8s_apps_client()
        core_v1 = self._get_k8s_core_client()

        # The data-download init container runs the storage backend's CLI image and
        # copy command (CopyStager seam): GCS -> google/cloud-sdk:slim + gcloud
        # storage; S3 -> amazon/aws-cli + aws s3 cp.
        from app.adapters.registry import get_storage_adapter

        storage = get_storage_adapter()
        staging_image = storage.staging_image()
        copy_cmd = storage.cli_copy_in(storage_uri, local_path)

        # Download auth: GCS service_account_key mode activates the mounted key
        # (cli_auth_command); GCS vm_default + AWS IRSA authenticate ambiently
        # (cli_auth_command == "") and do NOT mount the gcp-sa-key secret.
        data_mount = client.V1VolumeMount(name="data", mount_path="/data")
        volumes = [
            client.V1Volume(
                name="data",
                empty_dir=client.V1EmptyDirVolumeSource(size_limit="20Gi"),
            )
        ]
        if has_gcs_key:
            auth_cmd = storage.cli_auth_command("/gcp/key.json")
            download_cmd = f"{auth_cmd} && {copy_cmd}"
            init_volume_mounts = [data_mount, client.V1VolumeMount(name="gcp-sa", mount_path="/gcp", read_only=True)]
            volumes.append(
                client.V1Volume(
                    name="gcp-sa",
                    secret=client.V1SecretVolumeSource(secret_name="gcp-sa-key"),
                )
            )
        else:
            download_cmd = copy_cmd
            init_volume_mounts = [data_mount]

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
                                image=staging_image,
                                command=["/bin/sh", "-c", download_cmd],
                                volume_mounts=init_volume_mounts,
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
                        volumes=volumes,
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

        return CellxgeneInstance(
            publication_id=publication_id,
            status=ServiceState.STARTING,
            access_url=None,
            provider_details={"pod_name": name, "namespace": namespace},
        )

    async def teardown(self, publication_id: int) -> CellxgeneInstance:
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

        return CellxgeneInstance(
            publication_id=publication_id,
            status=ServiceState.STOPPED,
            provider_details={"stopped_at": datetime.now(timezone.utc).isoformat()},
        )

    async def get_status(self, publication_id: int) -> CellxgeneInstance:
        await self._get_api_client_async()

        name = f"cellxgene-{publication_id}"
        namespace = DEFAULT_CELLXGENE_NAMESPACE
        apps_v1 = self._get_k8s_apps_client()

        try:
            dep = apps_v1.read_namespaced_deployment_status(name=name, namespace=namespace)
            ready = dep.status.ready_replicas or 0
            status = ServiceState.RUNNING if ready >= 1 else ServiceState.STARTING
        except Exception:
            return CellxgeneInstance(
                publication_id=publication_id,
                status=ServiceState.UNKNOWN,
                provider_details={"pod_name": name},
            )

        return CellxgeneInstance(
            publication_id=publication_id,
            status=status,
            provider_details={"pod_name": name, "namespace": namespace},
        )

    # -- Cluster config --

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
        return self._gke.core_v1()

    def _get_k8s_apps_client(self):
        return self._gke.apps_v1()

    def _get_k8s_rbac_client(self):
        return self._gke.rbac_v1()

    # -- Namespace setup --

    async def ensure_cellxgene_namespace(
        self, namespace: str = DEFAULT_CELLXGENE_NAMESPACE, gcp_sa_email: str = ""
    ) -> None:
        """Ensure the cellxgene namespace and service account exist.

        When ``gcp_sa_email`` is provided, the runner SA is bound to that GCP
        service account via the Workload Identity annotation so vm_default pods
        get GCP credentials without a mounted key. The annotation is patched
        even on the already-set-up path, since the namespace may have been
        created before Workload Identity was configured.
        """
        from kubernetes.client.rest import ApiException

        if self._namespace_ready:
            if gcp_sa_email:
                self._patch_sa_annotation(self._get_k8s_core_client(), namespace, gcp_sa_email)
            return

        core_v1 = self._get_k8s_core_client()
        rbac_v1 = self._get_k8s_rbac_client()

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
                    name="bioaf-cellxgene-runner",
                    labels={"bioaf.io/managed": "true"},
                    annotations=sa_annotations or None,
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

    def _patch_sa_annotation(self, core_v1, namespace: str, gcp_sa_email: str) -> None:
        """Ensure the cellxgene-runner KSA carries the pod-identity binding.

        Used on the upgrade path where the namespace was created before pod
        identity was wired (no binding annotation on the existing KSA).
        """
        desired = self._pod_identity.pod_identity_annotations(gcp_sa_email)
        try:
            sa = core_v1.read_namespaced_service_account(name="bioaf-cellxgene-runner", namespace=namespace)
            current = sa.metadata.annotations or {}
            if desired and any(current.get(k) != v for k, v in desired.items()):
                core_v1.patch_namespaced_service_account(
                    name="bioaf-cellxgene-runner",
                    namespace=namespace,
                    body={"metadata": {"annotations": desired}},
                )
                logger.info("Patched pod-identity annotation on bioaf-cellxgene-runner")
        except Exception:
            logger.exception("Failed to patch pod-identity annotation on bioaf-cellxgene-runner")

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
            auth = api_client.default_headers.get("Authorization")
            if not auth:
                raise RuntimeError(
                    "K8s ApiClient has no Authorization header; _build_out_of_cluster_client did not set one."
                )
            headers = {"Authorization": auth}

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
