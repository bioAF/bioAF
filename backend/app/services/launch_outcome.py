"""What a pipeline run records when the compute provider does not accept its job.

Shared by the catalog and custom launch paths, which agree on this much: the run is failed with a
stable category and a plain-language message, and the provider's own error (which can name hosts,
namespaces, and internal job IDs) goes to the server log next to the run it belongs to, never onto
the run or into a notification.

Known gap, not handled here: a raised submission is not proof the provider refused the job.
Kubernetes submission creates resources before it returns. An error after the Job was accepted
(a timeout reading the response, a failure normalizing it) leaves a job running under a run this
module marks failed, and the same is true if the job is accepted and the caller's commit then
fails. For github_repo custom pipelines the SSH-key Secret is created before the Job, so a rejected
Job can leave that Secret behind. Nothing reconciles orphaned jobs or Secrets yet; that is deferred
follow-up work (pipeline launch plan, Delivery A item 7), not a guarantee this code makes.
"""

import logging
from datetime import datetime, timezone

from app.models.pipeline_run import PipelineRun

SUBMISSION_FAILED = "submission_failed"


def submission_failed_message(run_id: int) -> str:
    return (
        "The compute cluster did not accept this run, so it never started. "
        f"An administrator can find the technical details in the server logs for run {run_id}."
    )


def mark_submission_failed(run: PipelineRun, error: BaseException, logger: logging.Logger) -> None:
    """Fail ``run`` because its submission raised ``error``; log the technical detail."""
    run.status = "failed"
    run.failure_reason = SUBMISSION_FAILED
    run.error_message = submission_failed_message(run.id)
    run.completed_at = datetime.now(timezone.utc)
    logger.error(
        "Pipeline run %d submission failed (organization %s, user %s): %s",
        run.id,
        run.organization_id,
        run.submitted_by_user_id,
        error,
        exc_info=error,
    )
