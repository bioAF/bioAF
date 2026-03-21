import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.cellxgene_publication import CellxgenePublication
from app.models.file import File
from app.services.audit_service import log_action

logger = logging.getLogger("bioaf.cellxgene_service")


class CellxgeneService:
    @staticmethod
    async def publish_dataset(
        session: AsyncSession,
        org_id: int,
        user_id: int,
        file_id: int,
        experiment_id: int | None,
        dataset_name: str,
    ) -> CellxgenePublication:
        """Publish an h5ad file to cellxgene."""
        # Validate file exists and is h5ad
        result = await session.execute(select(File).where(File.id == file_id, File.organization_id == org_id))
        file = result.scalar_one_or_none()
        if not file:
            raise ValueError("File not found")
        if file.file_type != "h5ad":
            raise ValueError("Only h5ad files can be published to cellxgene")

        pub = CellxgenePublication(
            organization_id=org_id,
            file_id=file_id,
            experiment_id=experiment_id,
            dataset_name=dataset_name,
            status="publishing",
            published_by_user_id=user_id,
        )
        session.add(pub)
        await session.flush()

        # Generate stable URL
        pub.stable_url = f"/cellxgene/{pub.id}/"

        await log_action(
            session,
            user_id=user_id,
            entity_type="cellxgene_publication",
            entity_id=pub.id,
            action="publish",
            details={"dataset_name": dataset_name, "file_id": file_id},
        )

        # Deploy cellxgene pod (async, don't block response)
        try:
            await CellxgeneService._deploy_cellxgene_pod(pub.id, file.gcs_uri, dataset_name)
            pub.status = "published"
            pub.published_at = datetime.now(timezone.utc)
        except Exception as e:
            logger.error("Failed to deploy cellxgene pod for publication %d: %s", pub.id, e)
            pub.status = "failed"

        await session.flush()
        return pub

    @staticmethod
    async def unpublish_dataset(
        session: AsyncSession, org_id: int, publication_id: int, user_id: int
    ) -> CellxgenePublication | None:
        pub = await CellxgeneService.get_publication(session, org_id, publication_id)
        if not pub:
            return None

        pub.status = "unpublishing"
        await session.flush()

        try:
            await CellxgeneService._teardown_cellxgene_pod(publication_id)
            pub.status = "unpublished"
            pub.unpublished_at = datetime.now(timezone.utc)
        except Exception as e:
            logger.error("Failed to teardown cellxgene pod for publication %d: %s", publication_id, e)
            pub.status = "failed"

        await log_action(
            session,
            user_id=user_id,
            entity_type="cellxgene_publication",
            entity_id=publication_id,
            action="unpublish",
        )
        await session.flush()
        return pub

    @staticmethod
    async def list_publications(
        session: AsyncSession, org_id: int, experiment_id: int | None = None
    ) -> list[CellxgenePublication]:
        query = (
            select(CellxgenePublication)
            .options(
                selectinload(CellxgenePublication.file).selectinload(File.uploader),
                selectinload(CellxgenePublication.published_by),
            )
            .where(CellxgenePublication.organization_id == org_id)
        )
        if experiment_id:
            query = query.where(CellxgenePublication.experiment_id == experiment_id)
        query = query.order_by(CellxgenePublication.created_at.desc())

        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_publication(session: AsyncSession, org_id: int, publication_id: int) -> CellxgenePublication | None:
        result = await session.execute(
            select(CellxgenePublication)
            .options(
                selectinload(CellxgenePublication.file).selectinload(File.uploader),
                selectinload(CellxgenePublication.published_by),
            )
            .where(
                CellxgenePublication.id == publication_id,
                CellxgenePublication.organization_id == org_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_publishable_files(session: AsyncSession, org_id: int) -> list[File]:
        """Return h5ad files that have no active cellxgene publication."""
        # Subquery: file IDs with active publications
        active_pub_file_ids = (
            select(CellxgenePublication.file_id)
            .where(
                CellxgenePublication.organization_id == org_id,
                CellxgenePublication.status.in_(["publishing", "published", "running"]),
            )
            .correlate(None)
            .scalar_subquery()
        )

        result = await session.execute(
            select(File)
            .where(
                File.organization_id == org_id,
                File.file_type == "h5ad",
                File.id.notin_(active_pub_file_ids),
            )
            .order_by(File.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def _deploy_cellxgene_pod(publication_id: int, gcs_uri: str, dataset_name: str) -> None:
        """Deploy a cellxgene pod via Kubernetes API."""
        try:
            from kubernetes import client as k8s_client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            apps_v1 = k8s_client.AppsV1Api()
            core_v1 = k8s_client.CoreV1Api()

            name = f"cellxgene-{publication_id}"
            namespace = "bioaf-cellxgene"

            # Create deployment
            deployment = k8s_client.V1Deployment(
                metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
                spec=k8s_client.V1DeploymentSpec(
                    replicas=1,
                    selector=k8s_client.V1LabelSelector(match_labels={"app": name}),
                    template=k8s_client.V1PodTemplateSpec(
                        metadata=k8s_client.V1ObjectMeta(labels={"app": name}),
                        spec=k8s_client.V1PodSpec(
                            containers=[
                                k8s_client.V1Container(
                                    name="cellxgene",
                                    image="cellxgene:latest",
                                    args=["launch", "--host", "0.0.0.0", gcs_uri],
                                    ports=[k8s_client.V1ContainerPort(container_port=5005)],
                                )
                            ]
                        ),
                    ),
                ),
            )
            apps_v1.create_namespaced_deployment(namespace=namespace, body=deployment)

            # Create service
            service = k8s_client.V1Service(
                metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
                spec=k8s_client.V1ServiceSpec(
                    selector={"app": name},
                    ports=[k8s_client.V1ServicePort(port=80, target_port=5005)],
                    type="ClusterIP",
                ),
            )
            core_v1.create_namespaced_service(namespace=namespace, body=service)

            logger.info("Deployed cellxgene pod %s", name)
        except ImportError:
            logger.warning("kubernetes package not installed, skipping pod deployment")
        except Exception as e:
            logger.error("Kubernetes deployment failed: %s", e)
            raise

    @staticmethod
    async def _teardown_cellxgene_pod(publication_id: int) -> None:
        """Teardown cellxgene pod."""
        try:
            from kubernetes import client as k8s_client, config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                k8s_config.load_kube_config()

            apps_v1 = k8s_client.AppsV1Api()
            core_v1 = k8s_client.CoreV1Api()

            name = f"cellxgene-{publication_id}"
            namespace = "bioaf-cellxgene"

            apps_v1.delete_namespaced_deployment(name=name, namespace=namespace)
            core_v1.delete_namespaced_service(name=name, namespace=namespace)

            logger.info("Torn down cellxgene pod %s", name)
        except ImportError:
            logger.warning("kubernetes package not installed, skipping pod teardown")
        except Exception as e:
            logger.error("Kubernetes teardown failed: %s", e)
            raise
