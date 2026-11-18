"""Terminal-instruction eligibility and recorded identity for runs and notebook sessions.

The one place that reads a pipeline run or notebook session record and says which workload it
names, so the connect routes and the administrator inventory apply identical rules:

| Value | Preferred | Legacy fallbacks, in order |
| --- | --- | --- |
| Pipeline Job | ``compute_job_ref`` | ``provider_metadata.job_name``, ``k8s_job_name``, ``slurm_job_id`` |
| Notebook Pod | ``compute_job_ref`` | ``provider_metadata.pod_name``, ``k8s_pod_name`` |
| Namespace | ``provider_metadata.namespace`` | ``k8s_namespace``, then the adapter default |

Fallbacks fill absent values; populated values that disagree are refused, never resolved by
precedence. A pipeline with no recorded Job is refused (its name embeds a timestamp and cannot be
guessed). A notebook's ``slurm_job_id`` holds its canonical session id, not a pod name.
"""

from __future__ import annotations

from app.adapters.kubernetes.exec_access import is_namespace, is_resource_name
from app.adapters.models import ConnectionTarget
from app.exceptions import ConnectionTargetInvalidError, ConnectionUnavailableError
from app.models.notebook_session import NotebookSession
from app.models.pipeline_run import PipelineRun

PIPELINE_STATES = ("running",)
NOTEBOOK_STATES = ("running", "idle")
NOTEBOOK_TYPES = ("jupyter", "rstudio")


def _agreed(values: list, what: str, *, valid) -> str | None:
    """The single value the populated fields agree on, or None when none is populated."""
    present = []
    for value in values:
        if value is None or value == "":
            continue
        if not isinstance(value, str) or not valid(value):
            raise ConnectionTargetInvalidError(
                f"The recorded {what} for this analysis is not a valid Kubernetes name.", reason="invalid_identity"
            )
        present.append(value)
    if len(set(present)) > 1:
        raise ConnectionTargetInvalidError(
            f"This analysis has conflicting recorded {what} values, so bioAF cannot tell which workload it is.",
            reason="conflicting_identity",
        )
    return present[0] if present else None


def _namespace(record) -> str | None:
    meta = record.provider_metadata or {}
    return _agreed([meta.get("namespace"), record.k8s_namespace], "namespace", valid=is_namespace)


def pipeline_target(run: PipelineRun) -> ConnectionTarget:
    if run.status not in PIPELINE_STATES:
        raise ConnectionUnavailableError(
            f"This analysis is in the '{run.status}' state; terminal instructions need it to be running.",
            reason="not_running",
        )
    meta = run.provider_metadata or {}
    job = _agreed(
        [run.compute_job_ref, meta.get("job_name"), run.k8s_job_name, run.slurm_job_id],
        "job",
        valid=is_resource_name,
    )
    namespace = _namespace(run)
    if job is None:
        raise ConnectionTargetInvalidError(
            "This analysis has no recorded compute job, so bioAF cannot tell which workload it is.",
            reason="missing_identity",
        )
    return ConnectionTarget(kind="pipeline_job", record_id=run.id, provider_ref=job, namespace=namespace)


def notebook_target(nb: NotebookSession) -> ConnectionTarget:
    if nb.session_type not in NOTEBOOK_TYPES:
        raise ConnectionTargetInvalidError(
            "Terminal instructions are available only for Jupyter and RStudio sessions.",
            reason="unsupported_session_type",
        )
    if nb.status not in NOTEBOOK_STATES:
        raise ConnectionUnavailableError(
            f"This session is in the '{nb.status}' state; terminal instructions need it to be running or idle.",
            reason="not_running",
        )
    meta = nb.provider_metadata or {}
    pod = _agreed([nb.compute_job_ref, meta.get("pod_name"), nb.k8s_pod_name], "pod", valid=is_resource_name)
    return ConnectionTarget(
        kind="notebook_session",
        record_id=nb.id,
        provider_ref=pod,
        namespace=_namespace(nb),
        user_id=nb.user_id,
        session_type=nb.session_type,
    )
