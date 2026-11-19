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

from app.adapters.kubernetes.exec_access import (
    agreed_value,
    is_resource_name,
    recorded_namespace,
    recorded_notebook_identity,
)
from app.adapters.models import ConnectionTarget
from app.exceptions import ConnectionTargetInvalidError, ConnectionUnavailableError
from app.models.notebook_session import NotebookSession
from app.models.pipeline_run import PipelineRun

PIPELINE_STATES = ("running",)
NOTEBOOK_STATES = ("running", "idle")
NOTEBOOK_TYPES = ("jupyter", "rstudio")


# One rule set for every reader of a recorded identity (terminal access, stop, cleanup).
_agreed = agreed_value
_namespace = recorded_namespace


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
    pod, namespace = recorded_notebook_identity(nb)
    return ConnectionTarget(
        kind="notebook_session",
        record_id=nb.id,
        provider_ref=pod,
        namespace=namespace,
        user_id=nb.user_id,
        session_type=nb.session_type,
    )
