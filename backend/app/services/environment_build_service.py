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
from app.services.event_bus import event_bus
from app.services.event_types import ENVIRONMENT_BUILD_COMPLETED
from app.services.notebook_image_service import (
    AR_REPO_ID,
    _authorized_request,
    _get_credentials,
    _read_config,
    ensure_artifact_registry,
)

logger = logging.getLogger("bioaf.environment_build")

# Packer template for building GCE VM images with conda environments (ADR-043).
# Stored as a string constant; written to the build context at build time.
PACKER_VM_TEMPLATE = """\
packer {
  required_plugins {
    googlecompute = {
      version = ">= 1.1.0"
      source  = "github.com/hashicorp/googlecompute"
    }
  }
}

variable "project_id" {
  type = string
}

variable "zone" {
  type = string
}

variable "image_name" {
  type = string
}

variable "environment_yml_gcs" {
  type = string
}

variable "conda_env_name" {
  type    = string
  default = "bioaf"
}

source "googlecompute" "work_node" {
  project_id   = var.project_id
  zone         = var.zone
  machine_type = "e2-standard-4"

  source_image_family = "ubuntu-2204-lts"
  source_image_project_id = ["ubuntu-os-cloud"]

  image_name        = var.image_name
  image_description = "bioAF work node environment"
  image_family      = "bioaf-worknode"
  image_labels = {
    bioaf-managed = "true"
  }

  # The build VM is transient (Packer creates it, runs the provisioner,
  # destroys it). Use pd-standard so the 50 GB does not consume the
  # regional SSD_TOTAL_GB quota -- which is already under pressure from
  # the GKE pool nodes' pd-balanced boot disks (those count toward
  # SSD_TOTAL_GB too). The image artifact uploaded to GCE Image Service
  # is unaffected and still works for pd-ssd work-node boot disks at
  # launch time. See documentation/to-resolve.md for the quota story.
  disk_size = 50
  disk_type = "pd-standard"

  ssh_username = "packer"
}

build {
  sources = ["source.googlecompute.work_node"]

  # System packages
  provisioner "shell" {
    inline = [
      "sudo add-apt-repository -y universe",
      "sudo apt-get update",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server git tmux htop curl fail2ban",
      "sudo systemctl enable ssh",
      "sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config",
      "sudo sed -i 's/^PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config.d/*.conf 2>/dev/null || true",
      "echo 'PasswordAuthentication yes' | sudo tee /etc/ssh/sshd_config.d/99-bioaf-password-auth.conf",
    ]
  }

  # Install gcsfuse
  provisioner "shell" {
    inline = [
      "export GCSFUSE_REPO=gcsfuse-$(lsb_release -c -s)",
      "echo \\"deb https://packages.cloud.google.com/apt $GCSFUSE_REPO main\\" | sudo tee /etc/apt/sources.list.d/gcsfuse.list",
      "curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key add -",
      "sudo apt-get update && sudo apt-get install -y gcsfuse",
    ]
  }

  # Install miniforge (conda-forge only, no Anaconda TOS)
  provisioner "shell" {
    inline = [
      "wget -q https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -O /tmp/miniforge.sh",
      "sudo bash /tmp/miniforge.sh -b -p /opt/conda",
      "sudo chmod -R a+rx /opt/conda",
      "rm /tmp/miniforge.sh",
      "echo 'export PATH=/opt/conda/bin:$PATH' | sudo tee /etc/profile.d/conda.sh",
    ]
  }

  # Download environment.yml from GCS and create conda env
  provisioner "shell" {
    inline = [
      "export PATH=/opt/conda/bin:$PATH",
      "gsutil cp ${var.environment_yml_gcs} /tmp/environment.yml",
      "conda env create -f /tmp/environment.yml",
      "conda clean -afy",
      "rm /tmp/environment.yml",
    ]
  }

  # Install bioaf heartbeat agent
  provisioner "shell" {
    inline = [
      "sudo mkdir -p /etc/bioaf",
      "sudo mkdir -p /outputs /scratch",
    ]
  }

  # Cleanup
  provisioner "shell" {
    inline = [
      "sudo apt-get clean",
      "sudo rm -rf /var/lib/apt/lists/*",
    ]
  }
}
"""


def _get_vm_image_name(env_name: str, version_number: int, build_number: int) -> str:
    """Construct GCE image name for a work node environment."""
    safe_name = env_name.lower().replace(" ", "-").replace("_", "-")
    return f"bioaf-worknode-{safe_name}-v{version_number}-{build_number}"


def _get_vm_image_uri(project_id: str, env_name: str, version_number: int, build_number: int) -> str:
    """Construct GCE image self-link URI."""
    name = _get_vm_image_name(env_name, version_number, build_number)
    return f"projects/{project_id}/global/images/{name}"


# Template for wrapping a conda environment.yml in a Dockerfile.
#
# Google Cloud SDK is installed at the OS layer (parallel to how GCE work-node
# base images already carry it) so the pipeline entrypoint trap can sync
# /outputs/ to GCS without depending on whatever the user puts in their conda
# env. Without this, `gsutil`/`gcloud storage` are missing from the container
# and the output-sync trap silently no-ops.
CONDA_DOCKERFILE_TEMPLATE = """\
FROM continuumio/miniconda3:latest

RUN apt-get update && \\
    apt-get install -y --no-install-recommends curl gnupg ca-certificates apt-transport-https && \\
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \\
      | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \\
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \\
      > /etc/apt/sources.list.d/google-cloud-sdk.list && \\
    apt-get update && \\
    apt-get install -y --no-install-recommends google-cloud-cli && \\
    apt-get clean && rm -rf /var/lib/apt/lists/*

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

        # Route to the correct build pipeline based on environment type (ADR-043, ADR-045)
        # Notebooks and pipelines build Docker images via Cloud Build.
        # Work nodes build GCE VM images via Packer.
        if env.environment_type == "work_node":
            return await EnvironmentBuildService._build_vm_image(session, env, version, org_id, user_id, environment_id)

        return await EnvironmentBuildService._build_docker_image(session, env, version, org_id, user_id, environment_id)

    @staticmethod
    async def _build_docker_image(
        session: AsyncSession,
        env: Environment,
        version: EnvironmentVersion,
        org_id: int,
        user_id: int,
        environment_id: int,
    ) -> str:
        """Build a Docker container image via Cloud Build (notebook environments)."""
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
    async def _build_vm_image(
        session: AsyncSession,
        env: Environment,
        version: EnvironmentVersion,
        org_id: int,
        user_id: int,
        environment_id: int,
    ) -> str:
        """Build a GCE VM image via Cloud Build + Packer (work node environments, ADR-043)."""
        import yaml

        if version.definition_format != "conda":
            raise ValueError("Work node environments only support conda definition format")

        project_id = await _read_config(session, "gcp_project_id")
        region = await _read_config(session, "gcp_region")
        working_bucket = await _read_config(session, "working_bucket_name")

        if not project_id or project_id == "null":
            raise ValueError("GCP project not configured")
        if not region or region == "null":
            raise ValueError("GCP region not configured")
        if not working_bucket or working_bucket == "null":
            raise ValueError("Working bucket not configured")

        # Pick a zone for the Packer build VM, avoiding -a which is
        # often capacity-constrained.  The image itself is regional.
        import random

        zone_suffix = random.choice(["b", "c", "f"])
        build_zone = f"{region}-{zone_suffix}"

        # Extract conda env name from the YAML
        data = yaml.safe_load(version.definition_content)
        conda_env_name = data.get("name", "bioaf") if data else "bioaf"

        # Upload environment.yml to GCS
        from google.cloud import storage

        credentials = await _get_credentials(session)
        storage_client = storage.Client(project=project_id, credentials=credentials)
        bucket = storage_client.bucket(working_bucket)

        safe_name = env.name.lower().replace(" ", "-").replace("_", "-")
        env_yml_path = f"builds/{safe_name}/v{version.version_number}/environment.yml"
        blob = bucket.blob(env_yml_path)
        blob.upload_from_string(version.definition_content, content_type="text/yaml")
        env_yml_gcs = f"gs://{working_bucket}/{env_yml_path}"

        # Upload Packer template
        packer_path = f"builds/{safe_name}/v{version.version_number}/work_node.pkr.hcl"
        packer_blob = bucket.blob(packer_path)
        packer_blob.upload_from_string(PACKER_VM_TEMPLATE, content_type="text/plain")

        # Build image name and URI
        image_name = _get_vm_image_name(env.name, version.version_number, version.build_number)
        image_uri = _get_vm_image_uri(project_id, env.name, version.version_number, version.build_number)

        # Submit Cloud Build with Packer
        sa_email = await _read_config(session, "gcp_service_account_email")
        if not sa_email or sa_email == "null":
            sa_email = getattr(credentials, "service_account_email", None)

        build_url = f"https://cloudbuild.googleapis.com/v1/projects/{project_id}/builds"
        build_body: dict = {
            "steps": [
                {
                    "name": "gcr.io/cloud-builders/gsutil",
                    "args": ["cp", f"gs://{working_bucket}/{packer_path}", "/workspace/work_node.pkr.hcl"],
                },
                {
                    "name": "hashicorp/packer",
                    "args": ["init", "/workspace/work_node.pkr.hcl"],
                },
                {
                    "name": "hashicorp/packer",
                    "args": [
                        "build",
                        f"-var=project_id={project_id}",
                        f"-var=zone={build_zone}",
                        f"-var=image_name={image_name}",
                        f"-var=environment_yml_gcs={env_yml_gcs}",
                        f"-var=conda_env_name={conda_env_name}",
                        "/workspace/work_node.pkr.hcl",
                    ],
                },
            ],
            "options": {"machineType": "E2_HIGHCPU_8"},
            "timeout": "3600s",
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
            action="build_vm_image",
            details={
                "environment_id": environment_id,
                "version_number": version.version_number,
                "build_id": build_id,
                "image_name": image_name,
            },
        )

        logger.info(
            "Submitted Packer VM build %s for %s v%d",
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
        completed_versions: list[EnvironmentVersion] = []
        for version in building_versions:
            if not version.build_id:
                continue

            status = await check_build_status(session, project_id, version.build_id)

            if status == "SUCCESS":
                version.status = "ready"
                changed += 1
                completed_versions.append(version)
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

        for version in completed_versions:
            env_result = await session.execute(select(Environment).where(Environment.id == version.environment_id))
            env = env_result.scalar_one_or_none()
            if env is None:
                continue
            await event_bus.emit(
                ENVIRONMENT_BUILD_COMPLETED,
                {
                    "environment_id": env.id,
                    "environment_version_id": version.id,
                    "environment_type": env.environment_type,
                    "organization_id": env.organization_id,
                },
            )

        return changed

    @staticmethod
    async def get_build_logs_url(session: AsyncSession, project_id: str, build_id: str) -> str | None:
        """Get the Cloud Build logs URL for a build."""
        if not build_id:
            return None
        return f"https://console.cloud.google.com/cloud-build/builds/{build_id}?project={project_id}"
