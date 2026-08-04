# ADR-025: Automated Pipeline Triggering

**Status:** Accepted
**Date:** 2026-03-10
**Deciders:** Brent (repository owner), informed by feedback from computational biology practitioners

---

## Context

bioAF's original architecture required manual pipeline launches: a user selects a pipeline, configures parameters, picks input files, and clicks "Run." This is fine for small teams processing a handful of experiments, but practitioners report that even after a year or two, teams outgrow manual triggering. One team reported running approximately 500 pipeline runs per day.

The manual-only model creates a bottleneck: bioinformaticians spend significant time on the repetitive task of kicking off pipeline runs when their expertise should be directed at notebook-based downstream analysis. The desired state is that bench scientists act as a constant input source for new data, CROs deliver processed files automatically, and pipelines run without human intervention. Scientists only need to intervene when something goes wrong or when budget limits are reached.

A competing product (7Bridges) was cited as having two key limitations: no CLI interaction with running jobs, and no auto-run capability. bioAF should address both.

---

## Decision

Implement a three-mode pipeline triggering system: manual (existing), event-driven (triggered by new file ingest), and scheduled (cron-based). All three modes feed into the same pipeline submission path and are subject to budget-aware pre-flight checks with predictive cost estimation.

### Trigger Modes

**Manual (existing):**

- User selects pipeline, configures parameters, picks input files, clicks "Run"
- No changes to existing behavior
- Budget pre-flight check shows estimated cost and remaining budget before submission

**Event-driven:**

- Triggered when new files are ingested (ADR-024) that match the pipeline's input criteria
- Configurable per pipeline: which file types, which projects/experiments, which naming patterns
- Optional batching window: "Wait N minutes after the last matching file arrives before triggering." This prevents launching a pipeline for every individual file in a multi-file delivery. Default: 15 minutes.
- When the batching window expires, the trigger collects all matching files that arrived during the window and submits a single pipeline run with all of them as inputs

**Scheduled:**

- Triggered on a configurable cron schedule
- At each scheduled time, the system checks for unprocessed files matching the pipeline's input criteria
- If matching files exist, a pipeline run is submitted
- If no matching files exist, the scheduled check is a no-op (no empty runs)
- Schedule options exposed in the UI: daily at X time, daily at X and Y times, specific days of the week at X time, or custom cron expression for advanced users

### Trigger Configuration

Each pipeline in the catalog has a trigger configuration, managed through the Pipeline Scheduling page (under the Pipelines nav section):

```json
{
  "pipeline_id": "uuid-of-pipeline",
  "trigger_mode": "event_driven",
  "event_config": {
    "file_types": ["fastq", "fastq.gz"],
    "project_filter": ["uuid-of-project-x"],
    "experiment_filter": null,
    "batching_window_minutes": 15
  },
  "schedule_config": null,
  "parameter_defaults": {
    "genome": "GRCh38",
    "aligner": "STARsolo"
  },
  "budget_config": {
    "require_approval_on_budget_warning": true,
    "auto_queue_when_over_budget": true
  },
  "enabled": true
}
```

For scheduled triggers:

```json
{
  "trigger_mode": "scheduled",
  "event_config": null,
  "schedule_config": {
    "cron_expression": "0 6 * * 1-5",
    "timezone": "America/New_York",
    "file_types": ["fastq", "fastq.gz"],
    "project_filter": null,
    "min_files_to_trigger": 1
  }
}
```

### Budget-Aware Pre-Flight

Before any pipeline run is submitted (regardless of trigger mode), the system performs a budget pre-flight check:

1. **Estimate run cost:** Based on historical runs of this pipeline with similar input sizes (file count, total size). If no history exists, use a configurable default estimate. The estimate includes a confidence interval (default +/-15%, refined over time as accuracy data accumulates).

2. **Check remaining budget:** Current month spend (from GCP Billing API) + cost of all queued/running jobs + estimated cost of this run vs. monthly budget cap.

3. **Decision logic:**

| Condition | Manual Run | Event/Scheduled Run |
|---|---|---|
| Estimated cost within budget | Show estimate, allow submission | Submit automatically |
| Estimated cost might exceed budget (within confidence interval) | Show warning, require acknowledgment | Queue as "pending budget review", notify admins and comp_bio users |
| Estimated cost will exceed budget | Show warning, require acknowledgment | Queue as "pending budget review", notify admins and comp_bio users |
| Budget already exhausted | Show warning, require admin override | Queue as "pending budget review", notify admins |

1. **Queue processing when budget is constrained:** Queued runs are processed in order of submission. The system runs jobs sequentially until the next job's estimated cost would exceed the remaining budget. Remaining jobs stay queued with status "pending budget review." Notifications are sent listing: number of jobs that will run, number held, estimated budget shortfall.

2. **Estimate accuracy tracking:** After each run completes, the actual cost is compared to the estimate. Over time, the system adjusts its estimation model per pipeline to improve accuracy.

### Pipeline Run Lifecycle with Triggers

```text
File ingested (ADR-024)
       │
       ▼
  Trigger evaluation
  (match file type, project, experiment against active triggers)
       │
       ├─ No matching trigger → stop (file is cataloged but no pipeline run)
       │
       ├─ Event-driven trigger matched
       │     │
       │     ▼
       │   Batching window open?
       │     ├─ Yes → add file to batch, reset timer
       │     └─ No → start batching window timer
       │              │
       │              ▼ (timer expires)
       │           Collect all batched files
       │              │
       │              ▼
       │         Budget pre-flight check
       │              │
       │              ├─ Within budget → submit pipeline run
       │              └─ Over budget → queue as "pending budget review"
       │
       └─ Scheduled trigger matched
             │
             ▼ (at scheduled time)
          Check for unprocessed matching files
             │
             ├─ No files → no-op
             └─ Files found → budget pre-flight → submit or queue
```

### Database Schema

```sql
-- Pipeline trigger configuration
pipeline_triggers (
  id UUID PRIMARY KEY,
  pipeline_id UUID NOT NULL,  -- references pipeline catalog
  organization_id UUID NOT NULL REFERENCES organizations(id),
  trigger_mode VARCHAR(20) NOT NULL,  -- manual, event_driven, scheduled
  event_config JSONB,
  schedule_config JSONB,
  parameter_defaults JSONB NOT NULL DEFAULT '{}',
  budget_config JSONB NOT NULL DEFAULT '{}',
  enabled BOOLEAN NOT NULL DEFAULT true,
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
)

-- Trigger evaluation log (for debugging and audit)
trigger_evaluations (
  id UUID PRIMARY KEY,
  trigger_id UUID NOT NULL REFERENCES pipeline_triggers(id),
  evaluation_type VARCHAR(20) NOT NULL,  -- file_ingest, scheduled, manual
  matched_files JSONB,  -- file IDs that matched
  budget_check_result JSONB,  -- estimated cost, remaining budget, decision
  result VARCHAR(20) NOT NULL,  -- submitted, queued, skipped, no_files
  pipeline_run_id UUID REFERENCES pipeline_runs(id),  -- null if skipped
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
)

-- Budget tracking for cost estimation
pipeline_cost_history (
  id UUID PRIMARY KEY,
  pipeline_run_id UUID NOT NULL REFERENCES pipeline_runs(id),
  pipeline_name VARCHAR(255) NOT NULL,
  input_file_count INTEGER,
  input_total_bytes BIGINT,
  estimated_cost DECIMAL(10,2),
  actual_cost DECIMAL(10,2),
  estimation_error_pct DECIMAL(5,2),  -- (actual - estimated) / estimated * 100
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
)
```

### Notifications

| Event | Recipients | Severity |
|---|---|---|
| Auto-triggered pipeline run submitted | Experiment owner, trigger creator | Info |
| Scheduled pipeline run submitted | Experiment owner, trigger creator | Info |
| Pipeline run queued (budget warning) | Admins, comp_bio users | Warning |
| Pipeline run queued (budget exhausted) | Admins | Critical |
| Budget estimated to be exhausted mid-queue (N of M runs will execute, remaining held) | Admins, comp_bio users | Warning |
| Trigger evaluation failed (error) | Admins | Warning |
| Batching window closed, N files collected for run | Trigger creator | Info |

### UI: Pipeline Scheduling Page

A new page under the Pipelines nav section:

- **Trigger list:** All configured triggers with pipeline name, mode, status (enabled/disabled), match statistics (runs triggered in last 7/30 days), next scheduled run (for scheduled triggers)
- **Trigger editor:** Select pipeline, choose mode, configure filters and parameters, set budget behavior, test against recent ingest events
- **Queue viewer:** Runs currently queued as "pending budget review" with estimated cost, option to approve individually or in bulk
- **Cost estimation dashboard:** Per-pipeline estimation accuracy over time, average cost per run, trend

---

## Consequences

**Positive:**

- Eliminates the manual bottleneck for routine pipeline execution
- Bench scientists and CROs can continuously deliver data without waiting for bioinformaticians to trigger runs
- Budget-aware pre-flight prevents runaway costs from automated triggers
- The batching window prevents wasteful single-file runs from multi-file deliveries
- Estimation accuracy improves over time, making budget predictions more reliable
- All three trigger modes use the same submission path, ensuring consistent provenance, auditing, and monitoring

**Negative:**

- Automated triggers can produce a high volume of pipeline runs, increasing compute costs if budget guardrails are set too high
- The batching window adds latency between file arrival and pipeline start
- Misconfigured triggers (wrong file type filter, wrong project filter) can submit incorrect pipeline runs: the trigger test feature mitigates this but doesn't eliminate it
- Budget estimation is inherently approximate; edge cases may still result in runs exceeding budget

**Neutral:**

- Manual pipeline launching is unchanged: existing workflows continue to work
- The trigger configuration is stored per-pipeline, not globally, so different pipelines can have different trigger strategies

---

## References

- ADR-024 (GCS event-driven auto-ingest: provides the ingest events that drive event-driven triggers)
- ADR-023 (CRO naming profiles: determines which files match which projects/experiments)
- ADR-020 (BioAF Adapter Layer: pipeline submission goes through the compute adapter)
- ADR-021 (Kubernetes compute backend: where triggered pipeline runs execute)
