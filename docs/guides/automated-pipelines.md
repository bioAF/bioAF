# Automated Pipelines

The implemented auto-run workflow is configured per experiment. It stores a pipeline, parameters, reference
settings and a delay, then schedules work when manifest reconciliation marks a Sample's required files
complete. It is separate from manually launching a Pipeline Run.

Auto-ingest and manifest ingest are currently gated off during the Naming Profile redesign. An enabled
auto-run configuration does not make new bucket deliveries trigger runs while that gate is active. Use the
normal catalog/custom pipeline launch flow for current work.

## Experiment Auto-Run Configuration

The experiment detail's auto-run section lets users with `pipelines:launch` create, edit, enable/disable
and remove a configuration. Select an enabled Pipeline Catalog Entry, review its parameters and reference
settings, and choose a delay in minutes. The internal API exposes:

```text
GET, POST /api/experiments/{experiment_id}/auto-runs
PATCH, DELETE /api/experiments/{experiment_id}/auto-runs/{config_id}
GET /api/experiments/{experiment_id}/auto-runs/{config_id}/pending
```

Pending rows record the Sample, completion time, scheduled time, status and eventual Pipeline Run ID.
The background worker processes due pending runs. Existing pending work can therefore still be processed;
turning off incoming ingest is not equivalent to pausing every background job.

## Budget and Failure Behavior

The auto-run worker has its own budget guard. When an Organization's recorded spend reaches its auto-run
threshold (90% of the configured monthly budget), it disables auto-run configurations and cancels pending
runs with a notification. Inspect the configuration and pending/run records after a refusal or failure.
There is no general "approve over-budget run" queue promised by this guide.

Submission, input and provider failures remain visible through their run/pending state. Confirm the chosen
inputs, reference version, parameters and infrastructure readiness before resubmitting manually.

## Current Boundaries

The old guide described a general event/batch/cron trigger editor, a drag-and-drop priority queue, queue-wide
pause/preemption switches and dry-run trigger evaluation. Those controls are not the current exposed
workflow. Historical Pipeline Trigger models and [ADR-025](../../decisions/ADR-025-automated-pipeline-triggering.md)
record design intent, not implementation evidence.

See [Naming Profiles](naming-profiles.md), [Compute Stack Setup](compute-stack-setup.md) and
[Computational Biologist Guide](../user-guide-compbio.md) for the active workflows.
