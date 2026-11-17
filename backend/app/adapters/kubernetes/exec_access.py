"""Terminal instructions for a running pipeline head pod or notebook pod.

One owner for what both Kubernetes adapters need to describe a terminal target:

- ``cluster_access``: the provider-selected (GKE or EKS) first-time setup guide and the explicit
  kubectl context that guide creates, built from the deployment's own cluster configuration.
- ``render_exec_command``: the copyable ``kubectl exec`` command, with every dynamic argument
  validated as a Kubernetes name and quoted as a literal.
- ``resolve_pipeline_head`` / ``resolve_notebook_pod``: bounded metadata reads that prove the
  recorded workload is the one the database record describes and its main container is running.
  Nothing here executes a command inside a pod.

Provider failures are mapped here, at the adapter boundary, into the connection domain errors so
routes never see raw Kubernetes exceptions. Raw diagnostics go to the server log only.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.exceptions import (
    ConnectionTargetInvalidError,
)
from app.platform.shell_args import shell_join

logger = logging.getLogger("bioaf.adapters.kubernetes.exec_access")

SHELL = "/bin/sh"
PIPELINE_CONTAINER = "pipeline"
NOTEBOOK_CONTAINER = "notebook"
RESOLUTION_DEADLINE_SECONDS = 10.0

TargetKind = Literal["pipeline_job", "notebook_session"]

# DNS-1123 subdomain (Job/Pod names) and label (namespaces, containers). A leading letter or digit
# also keeps a value from ever being read as a kubectl option.
_SUBDOMAIN = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")

_CLUSTER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_GCP_PROJECT = re.compile(r"^[a-z0-9][a-z0-9.:-]{0,127}$")
_GCP_REGION = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
_GCP_ZONE = re.compile(r"^[a-z]+-[a-z]+[0-9]+-[a-z]$")
_AWS_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]+$")

_SETUP_INCOMPLETE = (
    "Terminal instructions are not available because bioAF's cluster setup is incomplete. "
    "Ask an administrator to finish cluster setup."
)


def is_resource_name(value: str | None) -> bool:
    return bool(value) and bool(_SUBDOMAIN.match(value))  # type: ignore[arg-type]


def is_namespace(value: str | None) -> bool:
    return bool(value) and bool(_LABEL.match(value))  # type: ignore[arg-type]


def _invalid_identity(what: str) -> ConnectionTargetInvalidError:
    return ConnectionTargetInvalidError(
        f"The recorded {what} for this analysis is not a valid Kubernetes name.", reason="invalid_identity"
    )


def render_exec_command(*, pod: str, namespace: str, container: str, context: str | None) -> str:
    """The copyable command: explicit context, namespace, container, and the fixed shell."""
    if not is_resource_name(pod):
        raise _invalid_identity("pod")
    if not is_namespace(namespace):
        raise _invalid_identity("namespace")
    if not is_namespace(container):
        raise _invalid_identity("container")
    words = ["kubectl"]
    if context:
        words += ["--context", context]
    words += ["-n", namespace, "exec", "-it", pod, "--container", container, "--", SHELL]
    return shell_join(words)


@dataclass(frozen=True)
class ClusterAccess:
    """The kubectl context a user's setup creates, and the guide that creates it."""

    context: str
    setup_guide: str


def _required(cfg: dict, key: str, pattern: re.Pattern) -> str:
    value = (cfg.get(key) or "").strip()
    if not value or not pattern.match(value):
        logger.warning("Terminal instructions refused: cluster setting %s is missing or malformed", key)
        raise ConnectionTargetInvalidError(_SETUP_INCOMPLETE, reason="cluster_setup_incomplete")
    return value


def cluster_access(backend: str, cfg: dict, namespace: str) -> ClusterAccess:
    """Setup guide + context for the active cluster-auth backend (``gke`` or ``eks``)."""
    if backend == "eks":
        cluster = _required(cfg, "gke_cluster_name", _CLUSTER_NAME)
        region = _required(cfg, "aws_region", _AWS_REGION)
        context = f"bioaf-{region}-{cluster}"
        steps = [
            "Install kubectl (https://kubernetes.io/docs/tasks/tools/) and the AWS CLI (https://aws.amazon.com/cli/).",
            "Sign in with your own AWS identity (for example, aws sso login with your profile). "
            "Your identity must have been granted access to this cluster.",
            "Add this cluster to your kubectl configuration:\n   "
            + shell_join(
                ["aws", "eks", "update-kubeconfig", "--name", cluster, "--region", region, "--alias", context]
            ),
        ]
        cloud = "Amazon EKS"
    elif backend == "gke":
        cluster = _required(cfg, "gke_cluster_name", _CLUSTER_NAME)
        project = _required(cfg, "gcp_project_id", _GCP_PROJECT)
        location = (cfg.get("gcp_region") or "").strip()
        if _GCP_ZONE.match(location):
            location_flag = "--zone"
        elif _GCP_REGION.match(location):
            location_flag = "--region"
        else:
            logger.warning("Terminal instructions refused: cluster setting gcp_region is missing or malformed")
            raise ConnectionTargetInvalidError(_SETUP_INCOMPLETE, reason="cluster_setup_incomplete")
        # The context name gcloud get-credentials writes.
        context = f"gke_{project}_{location}_{cluster}"
        steps = [
            "Install kubectl and the Google Cloud CLI (https://cloud.google.com/sdk/docs/install), "
            "then: gcloud components install gke-gcloud-auth-plugin",
            f"Sign in with your own Google account: gcloud auth login. Your account needs access to project {project}.",
            "Add this cluster to your kubectl configuration:\n   "
            + shell_join(
                [
                    "gcloud",
                    "container",
                    "clusters",
                    "get-credentials",
                    cluster,
                    location_flag,
                    location,
                    "--project",
                    project,
                ]
            ),
        ]
        cloud = "Google Kubernetes Engine"
    else:
        raise ConnectionTargetInvalidError(_SETUP_INCOMPLETE, reason="cluster_setup_incomplete")

    steps.append(
        "Check that you can see the workloads:\n   "
        + shell_join(["kubectl", "--context", context, "-n", namespace, "get", "pods"])
    )
    guide = f"First-time setup for terminal access ({cloud}):\n\n" + "\n".join(
        f"{i}. {step}" for i, step in enumerate(steps, start=1)
    )
    return ClusterAccess(context=context, setup_guide=guide)
