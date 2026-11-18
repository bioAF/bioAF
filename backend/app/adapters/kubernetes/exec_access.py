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

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from kubernetes.client.exceptions import ApiException

from app.adapters.kubernetes.cluster_location import is_gke_location, is_gke_zone
from app.adapters.models import ConnectionDescription, ConnectionTarget
from app.exceptions import (
    ConnectionProviderTimeoutError,
    ConnectionProviderUnavailableError,
    ConnectionTargetInvalidError,
    ConnectionUnavailableError,
)
from app.platform.shell_args import shell_join

logger = logging.getLogger("bioaf.adapters.kubernetes.exec_access")

SHELL = "/bin/sh"
PIPELINE_CONTAINER = "pipeline"
NOTEBOOK_CONTAINER = "notebook"
RESOLUTION_DEADLINE_SECONDS = 10.0
PIPELINE_RUN_LABEL = "bioaf.io/pipeline-run"


def backend_for_cluster_auth() -> str:
    """The active cluster-auth backend (``gke``/``eks``), from the existing cloud selection."""
    from app.platform.cloud_provider import backend_for

    return backend_for("cluster_auth")


# DNS-1123 subdomain (Job/Pod names) and label (namespaces, containers). A leading letter or digit
# also keeps a value from ever being read as a kubectl option.
_SUBDOMAIN = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")

_CLUSTER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_GCP_PROJECT = re.compile(r"^[a-z0-9][a-z0-9.:-]{0,127}$")
_AWS_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]+$")
_AWS_ACCOUNT = re.compile(r"^[0-9]{12}$")

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


def render_exec_command(*, pod: str, namespace: str, container: str, context: str | None, kind: str = "pod") -> str:
    """The copyable command: explicit context, namespace, container, and the fixed shell.

    ``kind="job"`` renders ``job/<name>`` for the legacy unverified string API only.
    """
    if not is_resource_name(pod):
        raise _invalid_identity("pod")
    if not is_namespace(namespace):
        raise _invalid_identity("namespace")
    if not is_namespace(container):
        raise _invalid_identity("container")
    words = ["kubectl", "exec", "-it"]
    if context:
        words += ["--context", context]
    words += ["-n", namespace, f"job/{pod}" if kind == "job" else pod, "--container", container, "--", SHELL]
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
    """Setup guide + context for the active cluster-auth backend (``gke`` or ``eks``).

    The context names the whole cluster identity, so a command copied for one cluster can never
    select another one the user configured later: GKE's own ``gke_{project}_{location}_{cluster}``,
    and on EKS an alias carrying the account, region, and cluster name.
    """
    if backend == "eks":
        cluster = _required(cfg, "gke_cluster_name", _CLUSTER_NAME)
        region = _required(cfg, "aws_region", _AWS_REGION)
        account = _required(cfg, "aws_account_id", _AWS_ACCOUNT)
        context = f"bioaf-{account}-{region}-{cluster}"
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
        # The deployed cluster's own location (a compute Terraform output), never gcp_region: a
        # compute deploy can override the region and then restore the installation default.
        location = (cfg.get("gke_cluster_location") or "").strip()
        if is_gke_location(location):
            location_flag = "--zone" if is_gke_zone(location) else "--region"
        else:
            logger.warning(
                "Terminal instructions refused: cluster setting gke_cluster_location is missing or malformed"
            )
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


async def discover_gke_location(
    cfg: dict, *, list_clusters: Callable[[str, float], object], session_factory, budget: Callable[[], float]
) -> None:
    """Fill a missing ``gke_cluster_location`` in ``cfg`` by cluster name (recorded for next time).

    ``budget`` returns the seconds left and is read just before the listing call, so it is never
    stale; it raises ``ConnectionProviderTimeoutError`` when none are left.

    Not found leaves it missing, so ``cluster_access`` refuses as setup incomplete. A provider
    failure is a safe provider error; its text goes nowhere but the error class to the log.
    """
    from app.adapters.kubernetes.cluster_location import (
        ClusterLocationUnknown,
        classify_provider_failure,
        resolve_gke_cluster_location,
    )

    try:
        await resolve_gke_cluster_location(
            cfg, list_clusters=list_clusters, session_factory=session_factory, timeout=budget
        )
    except ClusterLocationUnknown:
        return
    except ConnectionProviderTimeoutError:
        raise
    except Exception as exc:
        kind = classify_provider_failure(exc)
        logger.warning(
            "Locating the GKE cluster for terminal setup failed (%s, error_type=%s)", kind, type(exc).__name__
        )
        if kind == "denied":
            raise ConnectionProviderUnavailableError(
                "The cluster refused bioAF's check of this workload. Ask an administrator to review bioAF's "
                "cluster access.",
                reason="provider_denied",
            ) from None
        if kind == "timeout":
            raise ConnectionProviderTimeoutError(
                "The cluster took too long to answer. Try again in a moment.", reason="deadline_exceeded"
            ) from None
        raise ConnectionProviderUnavailableError(
            "bioAF could not reach the cluster to check this workload. Try again in a moment.",
            reason="provider_unavailable",
        ) from None


# -- Live resolution ---------------------------------------------------------------------------


def _unavailable(message: str, reason: str) -> ConnectionUnavailableError:
    return ConnectionUnavailableError(message, reason=reason)


def _mismatch() -> ConnectionTargetInvalidError:
    return ConnectionTargetInvalidError(
        "The workload in the cluster does not match this analysis's record, so bioAF will not give "
        "terminal instructions for it.",
        reason="identity_mismatch",
    )


def _container_running(pod, container: str) -> bool:
    for status in getattr(pod.status, "container_statuses", None) or []:
        if status.name == container:
            return bool(status.state and status.state.running)
    return False


def _owned_by(pod, uid: str) -> bool:
    return any(ref.kind == "Job" and ref.uid == uid for ref in (pod.metadata.owner_references or []))


class _Deadline:
    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    def remaining(self) -> float:
        left = self._end - time.monotonic()
        if left <= 0:
            raise ConnectionProviderTimeoutError(
                "The cluster took too long to answer. Try again in a moment.", reason="deadline_exceeded"
            )
        return left


def _resolve_pipeline_head(core, batch, target: ConnectionTarget, namespace: str, deadline: _Deadline) -> str:
    job = batch.read_namespaced_job(
        name=target.provider_ref, namespace=namespace, _request_timeout=deadline.remaining()
    )
    meta = job.metadata
    if (meta.labels or {}).get(PIPELINE_RUN_LABEL) != str(target.record_id) or not meta.uid:
        raise _mismatch()
    if meta.deletion_timestamp:
        raise _unavailable("This analysis's compute job is shutting down.", "workload_ending")
    pods = core.list_namespaced_pod(
        namespace=namespace, label_selector=f"job-name={target.provider_ref}", _request_timeout=deadline.remaining()
    ).items
    eligible = [
        p
        for p in pods
        if _owned_by(p, meta.uid) and not p.metadata.deletion_timestamp and _container_running(p, PIPELINE_CONTAINER)
    ]
    if not eligible:
        raise _unavailable("This analysis's pipeline container is not running.", "container_not_running")
    if len(eligible) > 1:
        raise _unavailable(
            "More than one pod claims to run this analysis, so bioAF cannot pick one.", "ambiguous_target"
        )
    return eligible[0].metadata.name


def _resolve_notebook_pod(core, target: ConnectionTarget, namespace: str, deadline: _Deadline) -> str:
    pod = core.read_namespaced_pod(name=target.provider_ref, namespace=namespace, _request_timeout=deadline.remaining())
    labels = pod.metadata.labels or {}
    expected = {
        "bioaf.io/session": str(target.record_id),
        "bioaf.io/user": str(target.user_id),
        "bioaf.io/type": target.session_type,
    }
    if any(labels.get(k) != v for k, v in expected.items()):
        raise _mismatch()
    if pod.metadata.deletion_timestamp:
        raise _unavailable("This session is shutting down.", "workload_ending")
    if not _container_running(pod, NOTEBOOK_CONTAINER):
        raise _unavailable("This session's notebook container is not running.", "container_not_running")
    return pod.metadata.name


def _urllib3_classes(exc: BaseException) -> set[str]:
    return {c.__name__ for c in type(exc).__mro__ if c.__module__.startswith("urllib3")}


def _is_timeout(exc: BaseException) -> bool:
    """A transport timeout from the Kubernetes client's HTTP layer (urllib3), or the builtin one.

    Classified by class name so bioAF does not import urllib3 directly (it is the kubernetes
    client's dependency, not ours). ``NewConnectionError`` subclasses ``ConnectTimeoutError`` in
    urllib3 2 but means the connection was refused, not slow.
    """
    names = _urllib3_classes(exc)
    if "MaxRetryError" in names and getattr(exc, "reason", None) is not None:
        return _is_timeout(exc.reason)
    if names:
        return "TimeoutError" in names and "NewConnectionError" not in names
    return isinstance(exc, TimeoutError)


def _provider_error(exc: Exception, target: ConnectionTarget) -> Exception:
    """Map a provider exception to a connection domain error; raw detail goes to the log only."""
    if isinstance(exc, ApiException):
        logger.warning(
            "Terminal target check failed: kind=%s record_id=%s status=%s", target.kind, target.record_id, exc.status
        )
        if exc.status == 404:
            return _unavailable("This analysis's workload no longer exists in the cluster.", "workload_missing")
        if exc.status in (401, 403):
            return ConnectionProviderUnavailableError(
                "The cluster refused bioAF's check of this workload. Ask an administrator to review bioAF's "
                "cluster access.",
                reason="provider_denied",
            )
    timed_out = _is_timeout(exc)
    if timed_out:
        logger.warning("Terminal target check timed out: kind=%s record_id=%s", target.kind, target.record_id)
        return ConnectionProviderTimeoutError(
            "The cluster took too long to answer. Try again in a moment.", reason="deadline_exceeded"
        )
    if not isinstance(exc, ApiException):
        logger.warning(
            "Terminal target check could not reach the cluster: kind=%s record_id=%s error_type=%s",
            target.kind,
            target.record_id,
            type(exc).__name__,
        )
    return ConnectionProviderUnavailableError(
        "bioAF could not reach the cluster to check this workload. Try again in a moment.",
        reason="provider_unavailable",
    )


async def resolve_connection(
    target: ConnectionTarget,
    *,
    default_namespace: str,
    backend: str,
    load_config: Callable[[Callable[[], float]], Awaitable[dict]],
    clients: Callable[[], tuple],
    deadline: float | None = None,
) -> ConnectionDescription:
    """Prove ``target`` is live and describe how to open a shell in it, within ``deadline`` seconds.

    ``clients`` returns ``(core_v1, batch_v1)`` and is called off the event loop (building a
    client can mint a token). Only metadata reads are made; nothing runs inside a pod.
    """
    if not target.provider_ref:
        raise ConnectionTargetInvalidError(
            "This analysis has no recorded workload, so bioAF cannot tell which one it is.", reason="missing_identity"
        )
    namespace = target.namespace or default_namespace
    if not is_resource_name(target.provider_ref):
        raise _invalid_identity("workload")
    if not is_namespace(namespace):
        raise _invalid_identity("namespace")
    clock = _Deadline(RESOLUTION_DEADLINE_SECONDS if deadline is None else deadline)

    try:
        # load_config gets the clock, read when its cloud call starts, so that call is bounded by
        # what is left then rather than by what was left before its database reads.
        cfg = await asyncio.wait_for(load_config(clock.remaining), timeout=clock.remaining())
    except asyncio.TimeoutError:
        raise ConnectionProviderTimeoutError(
            "The cluster took too long to answer. Try again in a moment.", reason="deadline_exceeded"
        ) from None
    access = cluster_access(backend, cfg or {}, namespace)

    def check() -> tuple[str, str]:
        try:
            core, batch = clients()
            if target.kind == "pipeline_job":
                return _resolve_pipeline_head(core, batch, target, namespace, clock), PIPELINE_CONTAINER
            return _resolve_notebook_pod(core, target, namespace, clock), NOTEBOOK_CONTAINER
        except (ConnectionTargetInvalidError, ConnectionUnavailableError, ConnectionProviderTimeoutError):
            raise
        except Exception as exc:  # provider boundary: every other failure becomes a safe domain error
            raise _provider_error(exc, target) from None

    try:
        pod, container = await asyncio.wait_for(asyncio.to_thread(check), timeout=clock.remaining())
    except asyncio.TimeoutError:
        logger.warning(
            "Terminal target check exceeded its deadline: kind=%s record_id=%s", target.kind, target.record_id
        )
        raise ConnectionProviderTimeoutError(
            "The cluster took too long to answer. Try again in a moment.", reason="deadline_exceeded"
        ) from None

    return ConnectionDescription(
        command=render_exec_command(pod=pod, namespace=namespace, container=container, context=access.context),
        provider_ref=target.provider_ref,
        pod_name=pod,
        namespace=namespace,
        container=container,
        context=access.context,
        setup_guide=access.setup_guide,
    )
