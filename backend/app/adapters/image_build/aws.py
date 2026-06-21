"""CodeBuild realization of the ImageBuild seam (Stage 6e).

The AWS sibling of ``GcpCloudBuildProvider``. The two clouds differ in one
structural way the seam hides: Cloud Build is serverless (you POST a build with
an inline source + steps), whereas CodeBuild needs a pre-provisioned *project*
(created by the ``image_build`` Terraform module, along with its IAM service
role). ``submit_build`` therefore ``StartBuild``s that project rather than
creating infrastructure.

Build-context handling: the project is created with source type ``NO_SOURCE``,
so CodeBuild does not try to fetch/unzip the context (its S3 source only accepts
ZIP/JAR, but the service layer produces a ``tar.gz`` for both clouds). Instead
the inline ``buildspecOverride`` pulls the ``tar.gz`` from S3 itself, extracts
it, ``docker build``s, and pushes to ECR. That keeps the cloud-blind service's
build-context format identical on GCP and AWS.

Status mapping: CodeBuild's native IN_PROGRESS/SUCCEEDED/FAILED/FAULT/STOPPED/
TIMED_OUT is mapped onto the neutral QUEUED/WORKING/SUCCESS/FAILURE/CANCELLED/
TIMEOUT contract so the service layer never branches on a cloud status string.

boto3 lives behind this adapter boundary; the VM authenticates ambiently through
its instance profile (no GCP-style credentials object is threaded), and the build
itself runs as the CodeBuild project's own service role (so ``build_sa`` is
unused on AWS).
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.image_build.base import ImageBuildProvider

logger = logging.getLogger("bioaf.image_build")

# CodeBuild native build status -> neutral BUILD_STATUSES contract.
_STATUS_MAP = {
    "IN_PROGRESS": "WORKING",
    "SUCCEEDED": "SUCCESS",
    "FAILED": "FAILURE",
    "FAULT": "FAILURE",
    "STOPPED": "CANCELLED",
    "TIMED_OUT": "TIMEOUT",
}

# Heavier compute for long builds (the R/Bioconductor notebook image; ~2h).
# CodeBuild timeouts are minute-granular, so the service's "<n>s" maps to minutes.
_HEAVY_BUILD_MINUTES = 90

# The inline buildspec the NO_SOURCE project runs. It fetches the build context
# (a tar.gz the service uploaded to S3), extracts it, logs in to ECR, builds the
# Dockerfile, and pushes the image. $CONTEXT_URI / $IMAGE_URI come from the
# per-build environment overrides; $AWS_DEFAULT_REGION drives the ECR login host.
_BUILDSPEC = """\
version: 0.2
phases:
  pre_build:
    commands:
      - aws s3 cp "$CONTEXT_URI" /tmp/context.tar.gz
      - mkdir -p /tmp/context && tar xzf /tmp/context.tar.gz -C /tmp/context
      - REGISTRY="$(echo "$IMAGE_URI" | cut -d/ -f1)"
      - aws ecr get-login-password --region "$AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
  build:
    commands:
      - docker build -t "$IMAGE_URI" -f /tmp/context/Dockerfile /tmp/context
  post_build:
    commands:
      - docker push "$IMAGE_URI"
"""


# The inline buildspec for a Packer AMI build (work-node environments). Unlike the
# container buildspec it does not docker-build / push to ECR; it pulls the build
# context (a tar.gz with the Packer template + environment.yml the service uploaded
# to S3), installs Packer, and runs ``packer build``. The amazon-ebs source creates
# the AMI; Packer reads its input variables from the ``PKR_VAR_*`` env overrides the
# provider sets per-build. The Packer builder instance launches in the account's
# default VPC (no compute dependency) and needs no IAM (the environment.yml is
# shipped to it via a Packer ``file`` provisioner, not pulled from S3 on the box).
_PACKER_VERSION = "1.11.2"
_PACKER_BUILDSPEC = f"""\
version: 0.2
phases:
  pre_build:
    commands:
      - aws s3 cp "$CONTEXT_URI" /tmp/context.tar.gz
      - mkdir -p /tmp/ctx && tar xzf /tmp/context.tar.gz -C /tmp/ctx
      - curl -fsSL -o /tmp/packer.zip "https://releases.hashicorp.com/packer/{_PACKER_VERSION}/packer_{_PACKER_VERSION}_linux_amd64.zip"
      - unzip -o /tmp/packer.zip -d /usr/local/bin
      - packer version
  build:
    commands:
      - cd /tmp/ctx
      - packer init work_node.pkr.hcl
      - packer build work_node.pkr.hcl
"""


def _timeout_minutes(timeout: str) -> int:
    """Parse the service's ``"<seconds>s"`` timeout into CodeBuild minutes."""
    try:
        seconds = int(timeout.rstrip("s"))
    except (ValueError, AttributeError):
        return 60
    return max(1, (seconds + 59) // 60)


class CodeBuildImageBuildProvider(ImageBuildProvider):
    """AWS CodeBuild: StartBuild a NO_SOURCE project, poll, stop."""

    def _client(self, region: str):
        """Construct the boto3 CodeBuild client (lazy import; ambient creds)."""
        import boto3

        return boto3.client("codebuild", region_name=region or None)

    def submit_build(
        self,
        credentials: Any,
        config: dict,
        *,
        context_object_uri: str,
        image_uri: str,
        build_sa: str | None,
        timeout: str,
    ) -> str:
        region = config["region"]
        project = config["codebuild_project"]
        minutes = _timeout_minutes(timeout)
        # Heavy builds (the notebook image, 2h) need more than the default
        # SMALL compute to docker-build R/Bioconductor in time.
        compute = "BUILD_GENERAL1_LARGE" if minutes >= _HEAVY_BUILD_MINUTES else "BUILD_GENERAL1_MEDIUM"

        cb = self._client(region)
        result = cb.start_build(
            projectName=project,
            buildspecOverride=_BUILDSPEC,
            computeTypeOverride=compute,
            timeoutInMinutesOverride=minutes,
            environmentVariablesOverride=[
                {"name": "CONTEXT_URI", "value": context_object_uri, "type": "PLAINTEXT"},
                {"name": "IMAGE_URI", "value": image_uri, "type": "PLAINTEXT"},
                {"name": "AWS_DEFAULT_REGION", "value": region, "type": "PLAINTEXT"},
            ],
        )
        build_id = result.get("build", {}).get("id", "")
        logger.info("Started CodeBuild %s for image %s (compute %s)", build_id, image_uri, compute)
        return build_id

    def submit_vm_image_build(
        self,
        credentials: Any,
        config: dict,
        *,
        context_object_uri: str,
        image_name: str,
        build_vars: dict[str, str],
        timeout: str,
    ) -> str:
        region = config["region"]
        project = config["codebuild_project"]
        minutes = _timeout_minutes(timeout)

        # Packer reads PKR_VAR_<name> from the environment, so each build var is
        # passed as a CodeBuild env override rather than a -var flag.
        env_overrides = [
            {"name": "CONTEXT_URI", "value": context_object_uri, "type": "PLAINTEXT"},
            {"name": "AWS_DEFAULT_REGION", "value": region, "type": "PLAINTEXT"},
        ]
        env_overrides += [{"name": f"PKR_VAR_{k}", "value": v, "type": "PLAINTEXT"} for k, v in build_vars.items()]

        cb = self._client(region)
        result = cb.start_build(
            projectName=project,
            buildspecOverride=_PACKER_BUILDSPEC,
            # Packer drives a separate builder EC2 instance, so the CodeBuild
            # container itself stays MEDIUM (it only runs the packer CLI).
            computeTypeOverride="BUILD_GENERAL1_MEDIUM",
            timeoutInMinutesOverride=minutes,
            environmentVariablesOverride=env_overrides,
        )
        build_id = result.get("build", {}).get("id", "")
        logger.info("Started CodeBuild Packer AMI build %s for image %s", build_id, image_name)
        return build_id

    def check_build_status(self, credentials: Any, config: dict, build_id: str) -> str:
        try:
            cb = self._client(config["region"])
            builds = cb.batch_get_builds(ids=[build_id]).get("builds", [])
            if not builds:
                return "UNKNOWN"
            native = builds[0].get("buildStatus", "")
            return _STATUS_MAP.get(native, "UNKNOWN")
        except Exception as e:
            logger.error("Failed to check CodeBuild %s: %s", build_id, e)
            return "UNKNOWN"

    def cancel_build(self, credentials: Any, config: dict, build_id: str) -> None:
        try:
            self._client(config["region"]).stop_build(id=build_id)
        except Exception as e:
            logger.warning("CodeBuild stop returned error (may already be done): %s", e)
