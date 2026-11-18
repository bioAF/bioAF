"""Which workload a pipeline run or notebook session record names, under the shared identity rules.

Fixtures mirror what launch actually persists (``record_submission_accepted`` for pipelines,
``NotebookService.launch_session`` for notebooks) plus the legacy column-only shapes. Pure: the
records are unsaved model instances.
"""

import pytest

from app.adapters.models import JobSubmitResult
from app.exceptions import ConnectionTargetInvalidError, ConnectionUnavailableError
from app.models.notebook_session import NotebookSession
from app.models.pipeline_run import PipelineRun
from app.services.connection_access import notebook_target, pipeline_target
from app.services.launch_outcome import record_submission_accepted

JOB = "bioaf-pipeline-42-1731700000"


def _run(**fields) -> PipelineRun:
    run = PipelineRun(id=42, organization_id=1, pipeline_name="nf-core/rnaseq", status="running")
    for k, v in fields.items():
        setattr(run, k, v)
    return run


def _launched_run(namespace="bioaf-pipelines") -> PipelineRun:
    run = _run()
    record_submission_accepted(
        run, JobSubmitResult(job_id=JOB, status="running", provider_details={"namespace": namespace})
    )
    return run


def _nb(**fields) -> NotebookSession:
    s = NotebookSession(id=7, user_id=3, organization_id=1, session_type="jupyter", status="running")
    for k, v in fields.items():
        setattr(s, k, v)
    return s


def _launched_nb(**fields) -> NotebookSession:
    # NotebookService.launch_session: slurm_job_id is the canonical session id, never a pod.
    launched = {
        "slurm_job_id": "7",
        "k8s_pod_name": "bioaf-notebook-7",
        "k8s_namespace": "bioaf-notebooks",
        "compute_job_ref": "bioaf-notebook-7",
        "provider_metadata": {"pod_name": "bioaf-notebook-7", "namespace": "bioaf-notebooks"},
    }
    return _nb(**{**launched, **fields})


# -- pipelines ---------------------------------------------------------------------------------


def test_launched_pipeline_names_its_timestamped_job_and_recorded_namespace():
    target = pipeline_target(_launched_run(namespace="team-pipelines"))
    assert target.kind == "pipeline_job"
    assert target.record_id == 42
    assert target.provider_ref == JOB
    assert target.namespace == "team-pipelines"


@pytest.mark.parametrize(
    "fields",
    [
        {"provider_metadata": {"job_name": JOB}},
        {"k8s_job_name": JOB},
        {"slurm_job_id": JOB},
    ],
)
def test_pipeline_legacy_fallbacks_fill_a_missing_reference(fields):
    assert pipeline_target(_run(**fields)).provider_ref == JOB


def test_pipeline_namespace_falls_back_to_legacy_column_then_adapter_default():
    assert pipeline_target(_run(compute_job_ref=JOB, k8s_namespace="legacy-ns")).namespace == "legacy-ns"
    assert pipeline_target(_run(compute_job_ref=JOB, k8s_namespace="")).namespace is None


def test_pipeline_without_any_recorded_job_is_refused_not_guessed():
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        pipeline_target(_run())
    assert exc.value.reason == "missing_identity"


@pytest.mark.parametrize(
    "fields",
    [
        {"compute_job_ref": JOB, "k8s_job_name": "bioaf-pipeline-42-1731799999"},
        {"compute_job_ref": JOB, "provider_metadata": {"job_name": "bioaf-pipeline-99-1"}},
        {"compute_job_ref": JOB, "provider_metadata": {"namespace": "a"}, "k8s_namespace": "b"},
    ],
)
def test_pipeline_conflicting_recorded_identity_is_refused(fields):
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        pipeline_target(_run(**fields))
    assert exc.value.reason == "conflicting_identity"


@pytest.mark.parametrize(
    "fields",
    [
        {"compute_job_ref": "job; rm -rf /"},
        {"compute_job_ref": JOB, "k8s_namespace": "ns$(id)"},
        {"compute_job_ref": JOB, "provider_metadata": {"namespace": ["not", "a", "string"]}},
    ],
)
def test_pipeline_malformed_identity_is_refused(fields):
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        pipeline_target(_run(**fields))
    assert exc.value.reason == "invalid_identity"


@pytest.mark.parametrize("status", ["pending", "queued", "completed", "failed", "cancelled"])
def test_only_running_pipelines_are_eligible(status):
    run = _launched_run()
    run.status = status
    with pytest.raises(ConnectionUnavailableError) as exc:
        pipeline_target(run)
    assert exc.value.reason == "not_running"
    assert status in str(exc.value)


# -- notebooks ---------------------------------------------------------------------------------


def test_launched_notebook_names_its_pod_owner_and_type():
    target = notebook_target(_launched_nb())
    assert target.kind == "notebook_session"
    assert target.provider_ref == "bioaf-notebook-7"
    assert target.namespace == "bioaf-notebooks"
    assert target.user_id == 3
    assert target.session_type == "jupyter"


@pytest.mark.parametrize(
    "fields", [{"provider_metadata": {"pod_name": "bioaf-notebook-7"}}, {"k8s_pod_name": "bioaf-notebook-7"}]
)
def test_notebook_legacy_pod_fallbacks(fields):
    assert notebook_target(_nb(**fields)).provider_ref == "bioaf-notebook-7"


def test_notebook_slurm_job_id_is_never_a_pod_reference():
    target = notebook_target(_nb(slurm_job_id="7"))
    assert target.provider_ref is None  # the adapter applies its launch rule to the record id
    assert target.namespace is None


def test_notebook_conflicting_pod_is_refused():
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        notebook_target(_launched_nb(k8s_pod_name="bioaf-notebook-8"))
    assert exc.value.reason == "conflicting_identity"


@pytest.mark.parametrize("session_type", ["jupyter", "rstudio"])
@pytest.mark.parametrize("status", ["running", "idle"])
def test_jupyter_and_rstudio_running_or_idle_are_eligible(session_type, status):
    assert notebook_target(_launched_nb(session_type=session_type, status=status)).session_type == session_type


@pytest.mark.parametrize("session_type", ["ssh", "headless", "cellxgene"])
def test_work_nodes_and_headless_sessions_are_not_terminal_targets(session_type):
    with pytest.raises(ConnectionTargetInvalidError) as exc:
        notebook_target(_launched_nb(session_type=session_type))
    assert exc.value.reason == "unsupported_session_type"


@pytest.mark.parametrize("status", ["pending", "starting", "stopping", "stopped", "failed"])
def test_notebooks_outside_running_or_idle_are_unavailable(status):
    with pytest.raises(ConnectionUnavailableError) as exc:
        notebook_target(_launched_nb(status=status))
    assert exc.value.reason == "not_running"
