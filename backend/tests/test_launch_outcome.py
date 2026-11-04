"""What a run records about its submission, shared by the catalog and custom launch paths.

An accepted job is recorded under the backend-neutral handle and, until the old columns are
dropped, the legacy Kubernetes/SLURM ones too, because the monitor and cancellation still read
either. A refused job is failed with a stable category and a plain message.
"""

import logging
from decimal import Decimal

from app.adapters.models import CostEstimate, JobSubmitResult
from app.models.pipeline_run import PipelineRun
from app.services.launch_outcome import SUBMISSION_FAILED, mark_submission_failed, record_submission_accepted


def _run() -> PipelineRun:
    return PipelineRun(id=5, organization_id=1, submitted_by_user_id=2, pipeline_name="p", status="pending")


def test_an_accepted_submission_records_neutral_and_legacy_handles():
    run = _run()
    record_submission_accepted(
        run,
        JobSubmitResult(
            job_id="bioaf-pipeline-5-17",
            status="queued",
            provider_details={"namespace": "bioaf-pipelines", "node_pool": None},
            estimated_cost=CostEstimate(estimated_cost_usd=1.25, currency="USD", basis="x"),
        ),
    )

    assert run.status == "running"
    assert run.started_at is not None
    assert run.compute_job_ref == run.k8s_job_name == run.slurm_job_id == "bioaf-pipeline-5-17"
    assert run.k8s_namespace == "bioaf-pipelines"
    assert run.provider_metadata == {"job_name": "bioaf-pipeline-5-17", "namespace": "bioaf-pipelines"}
    assert Decimal(str(run.cost_estimate)) == Decimal("1.25")


def test_an_accepted_submission_without_a_namespace_or_cost_leaves_them_empty():
    run = _run()
    record_submission_accepted(run, JobSubmitResult(job_id="j", status="queued"))
    assert run.k8s_namespace == ""
    assert run.cost_estimate is None
    assert run.provider_metadata == {"job_name": "j"}


def test_a_refused_submission_is_failed_with_a_plain_message(caplog):
    run = _run()
    with caplog.at_level(logging.ERROR):
        mark_submission_failed(run, RuntimeError("10.0.0.7: connection refused"), logging.getLogger("t"))

    assert run.status == "failed"
    assert run.failure_reason == SUBMISSION_FAILED
    assert "10.0.0.7" not in run.error_message
    assert "run 5" in run.error_message
    assert run.started_at is None and run.completed_at is not None
    assert "10.0.0.7" in caplog.text


def test_the_failure_message_does_not_claim_the_job_never_ran():
    """A raised submission is not proof the provider refused the job: it may have been accepted
    before the error (a timeout reading the response, say). The message must not say otherwise."""
    run = _run()
    mark_submission_failed(run, TimeoutError("read timed out"), logging.getLogger("t"))

    message = run.error_message.lower()
    for overclaim in ("never started", "did not accept", "was not accepted", "rejected"):
        assert overclaim not in message
    assert "could not confirm" in message
    assert "run 5" in message
