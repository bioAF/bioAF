"""Environment build service.

Generalizes NotebookImageService to build container images from
environment version definitions (Dockerfile or conda YAML).
Uses Cloud Build REST APIs with google-auth credentials.
"""

from __future__ import annotations

import io
import logging
import tarfile
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.environment import Environment
from app.models.environment_version import EnvironmentVersion
from app.services.audit_service import log_action
from app.services.notebook_image_service import (
    AR_REPO_ID,
    _authorized_request,
    _get_credentials,
    _read_config,
    ensure_artifact_registry,
)

logger = logging.getLogger("bioaf.environment_build")

# Template for wrapping a conda environment.yml in a Dockerfile
CONDA_DOCKERFILE_TEMPLATE = """\
FROM continuumio/miniconda3:latest

COPY environment.yml /tmp/environment.yml
RUN conda env create -f /tmp/environment.yml && \\
    conda clean -afy

# Activate the conda environment by default
SHELL ["conda", "run", "-n", "{env_name}", "/bin/bash", "-c"]
ENV PATH /opt/conda/envs/{env_name}/bin:$PATH

WORKDIR /home
"""


def _get_image_uri(project_id: str, region: str, env_name: str, version_number: int, build_number: int = 1) -> str:
    """Construct Artifact Registry image URI with version.build tag."""
    # Sanitize env_name for use in image tag (lowercase, hyphens only)
    safe_name = env_name.lower().replace(" ", "-").replace("_", "-")
    tag = f"v{version_number}.{build_number}"
    return f"{region}-docker.pkg.dev/{project_id}/{AR_REPO_ID}/{safe_name}:{tag}"


def _build_conda_dockerfile(definition_content: str, env_name: str) -> tuple[str, str]:
    """Generate a Dockerfile from a conda environment.yml.

    Returns (dockerfile_content, environment_yml_content).
    """
    import yaml

    # Parse to extract the conda env name
    data = yaml.safe_load(definition_content)
    conda_env_name = data.get("name", "base") if data else "base"

    dockerfile = CONDA_DOCKERFILE_TEMPLATE.format(env_name=conda_env_name)
    return dockerfile, definition_content


async def _upload_version_build_context(
    session: AsyncSession,
    project_id: str,
    working_bucket: str,
    version: EnvironmentVersion,
    env_name: str,
) -> str:
    """Create a tar.gz with the build context and upload to GCS."""
    from google.cloud import storage

    credentials = await _get_credentials(session)
    client = storage.Client(project=project_id, credentials=credentials)
    bucket = client.bucket(working_bucket)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if version.definition_format == "conda":
            dockerfile_content, env_yml = _build_conda_dockerfile(version.definition_content, env_name)
            # Add Dockerfile
            df_bytes = dockerfile_content.encode()
            info = tarfile.TarInfo(name="Dockerfile")
            info.size = len(df_bytes)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(df_bytes))

            # Add environment.yml
            yml_bytes = env_yml.encode()
            info2 = tarfile.TarInfo(name="environment.yml")
            info2.size = len(yml_bytes)
            info2.mtime = int(time.time())
            tar.addfile(info2, io.BytesIO(yml_bytes))
        else:
            # Dockerfile format -- definition_content IS the Dockerfile
            df_bytes = version.definition_content.encode()
            info = tarfile.TarInfo(name="Dockerfile")
            info.size = len(df_bytes)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(df_bytes))

    buf.seek(0)
    safe_name = env_name.lower().replace(" ", "-").replace("_", "-")
    object_path = f"builds/{safe_name}/v{version.version_number}/source.tar.gz"
    blob = bucket.blob(object_path)
    blob.upload_from_file(buf, content_type="application/gzip")
    logger.info("Uploaded build context to gs://%s/%s", working_bucket, object_path)

    return object_path


class EnvironmentBuildService:
    @staticmethod
    async def build_version(
        session: AsyncSession, org_id: int, user_id: int, environment_id: int, version_id: int
    ) -> str:
        """Submit a Cloud Build job for an environment version.

        Returns the Cloud Build ID.
        """
        # Load environment and version
        env_result = await session.execute(
            select(Environment).where(
                Environment.id == environment_id,
                Environment.organization_id == org_id,
            )
        )
        env = env_result.scalar_one_or_none()
        if not env:
            raise ValueError("Environment not found")

        ver_result = await session.execute(
            select(EnvironmentVersion).where(
                EnvironmentVersion.id == version_id,
                EnvironmentVersion.environment_id == environment_id,
            )
        )
        version = ver_result.scalar_one_or_none()
        if not version:
            raise ValueError("Version not found")

        if version.status not in ("draft", "failed"):
            raise ValueError(f"Cannot build version in '{version.status}' status")

        project_id = await _read_config(session, "gcp_project_id")
        region = await _read_config(session, "gcp_region")
        working_bucket = await _read_config(session, "working_bucket_name")

        if not project_id or project_id == "null":
            raise ValueError("GCP project not configured")
        if not region or region == "null":
            raise ValueError("GCP region not configured")
        if not working_bucket or working_bucket == "null":
            raise ValueError("Working bucket not configured")

        # Ensure Artifact Registry repo exists
        await ensure_artifact_registry(session, project_id, region)

        # Upload build context
        object_path = await _upload_version_build_context(session, project_id, working_bucket, version, env.name)

        # Build image URI
        image_uri = _get_image_uri(project_id, region, env.name, version.version_number, version.build_number)

        # Submit Cloud Build
        credentials = await _get_credentials(session)

        sa_email = await _read_config(session, "gcp_service_account_email")
        if not sa_email or sa_email == "null":
            sa_email = getattr(credentials, "service_account_email", None)

        build_url = f"https://cloudbuild.googleapis.com/v1/projects/{project_id}/builds"
        build_body: dict = {
            "source": {
                "storageSource": {
                    "bucket": working_bucket,
                    "object": object_path,
                }
            },
            "steps": [
                {
                    "name": "gcr.io/cloud-builders/docker",
                    "args": ["build", "-t", image_uri, "-f", "Dockerfile", "."],
                }
            ],
            "images": [image_uri],
            "options": {"machineType": "E2_HIGHCPU_8"},
            "timeout": "7200s",
        }
        if sa_email and sa_email != "null":
            build_body["serviceAccount"] = f"projects/{project_id}/serviceAccounts/{sa_email}"
            build_body["options"]["defaultLogsBucketBehavior"] = "REGIONAL_USER_OWNED_BUCKET"

        result = _authorized_request(credentials, "POST", build_url, build_body)
        build_id = result.get("metadata", {}).get("build", {}).get("id", "")

        # Update version record
        version.status = "building"
        version.build_id = build_id
        version.image_uri = image_uri
        await session.flush()

        await log_action(
            session,
            user_id=user_id,
            entity_type="environment_version",
            entity_id=version.id,
            action="build",
            details={
                "environment_id": environment_id,
                "version_number": version.version_number,
                "build_id": build_id,
            },
        )

        logger.info(
            "Submitted Cloud Build %s for %s v%d",
            build_id,
            env.name,
            version.version_number,
        )
        return build_id

    @staticmethod
    async def poll_in_progress_builds(session: AsyncSession) -> int:
        """Poll all in-progress environment builds and update statuses.

        Returns the number of builds that changed status.
        """
        from app.services.notebook_image_service import check_build_status

        result = await session.execute(select(EnvironmentVersion).where(EnvironmentVersion.status == "building"))
        building_versions = list(result.scalars().all())

        if not building_versions:
            return 0

        project_id = await _read_config(session, "gcp_project_id")
        if not project_id or project_id == "null":
            return 0

        changed = 0
        for version in building_versions:
            if not version.build_id:
                continue

            status = await check_build_status(session, project_id, version.build_id)

            if status == "SUCCESS":
                version.status = "ready"
                changed += 1
                logger.info(
                    "Build %s succeeded for environment version %d",
                    version.build_id,
                    version.id,
                )
                await log_action(
                    session,
                    user_id=None,
                    entity_type="environment_version",
                    entity_id=version.id,
                    action="build_succeeded",
                    details={
                        "build_id": version.build_id,
                        "version_number": version.version_number,
                    },
                )
            elif status in ("FAILURE", "CANCELLED", "TIMEOUT"):
                version.status = "failed"
                changed += 1
                logger.error(
                    "Build %s failed (%s) for environment version %d",
                    version.build_id,
                    status,
                    version.id,
                )
                await log_action(
                    session,
                    user_id=None,
                    entity_type="environment_version",
                    entity_id=version.id,
                    action="build_failed",
                    details={
                        "build_id": version.build_id,
                        "version_number": version.version_number,
                        "cloud_build_status": status,
                    },
                )

        if changed:
            await session.flush()

        return changed

    @staticmethod
    async def get_build_logs_url(session: AsyncSession, project_id: str, build_id: str) -> str | None:
        """Get the Cloud Build logs URL for a build."""
        if not build_id:
            return None
        return f"https://console.cloud.google.com/cloud-build/builds/{build_id}?project={project_id}"
