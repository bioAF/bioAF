"""The pipeline-run API exposes backend-neutral compute fields (BAL Phase 4).

compute_job_ref + provider_metadata are surfaced on the run response so the UI
can render a backend-neutral run detail with a provider-details disclosure,
rather than reading k8s_* directly. The k8s_* fields remain during the
transition (dropped once the frontend reads only the neutral fields).
"""

from types import SimpleNamespace

from app.api.pipeline_runs import _run_response


def _fake_run(**overrides):
    base = dict(
        id=1,
        pipeline_name="nf-core/scrnaseq",
        pipeline_version="2.7.1",
        experiment=None,
        submitted_by=None,
        status="running",
        parameters_json=None,
        input_files_json=None,
        output_files_json=None,
        progress_json=None,
        cost_estimate=None,
        error_message=None,
        work_dir=None,
        slurm_job_id="job-abc",
        k8s_job_name="job-abc",
        k8s_namespace="bioaf-pipelines",
        k8s_pod_name="pod-xyz",
        compute_job_ref="job-abc",
        provider_metadata={"job_name": "job-abc", "namespace": "bioaf-pipelines", "pod_name": "pod-xyz"},
        actual_cost=None,
        reference_genome=None,
        alignment_algorithm=None,
        resume_from_run_id=None,
        custom_pipeline_version_id=None,
        failure_reason=None,
        started_at=None,
        completed_at=None,
        created_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_response_exposes_neutral_compute_fields():
    resp = _run_response(_fake_run())
    assert resp.compute_job_ref == "job-abc"
    assert resp.provider_metadata == {
        "job_name": "job-abc",
        "namespace": "bioaf-pipelines",
        "pod_name": "pod-xyz",
    }


def test_neutral_fields_default_to_none_when_unset():
    resp = _run_response(_fake_run(compute_job_ref=None, provider_metadata=None))
    assert resp.compute_job_ref is None
    assert resp.provider_metadata is None
