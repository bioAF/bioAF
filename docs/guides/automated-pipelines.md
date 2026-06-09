# Automated Pipelines

bioAF supports fully automated pipeline execution so that data flows from CRO delivery through alignment, QC, and initial processing without manual intervention. This guide covers trigger modes, batching, scheduling, budget controls, and queue management.

## Trigger Modes

bioAF offers three trigger modes for automated pipelines. Each mode is configured per Pipeline Catalog Entry.

### Event-Driven Triggers

Event-driven triggers launch a pipeline run immediately when a qualifying event occurs. The most common event is file arrival via Auto-ingest.

To configure an event-driven trigger:

1. Navigate to **Pipelines > Definitions** and select the pipeline you want to automate.
2. Click the "Triggers" tab and select "Add Trigger."
3. Choose "Event-Driven" as the trigger type.
4. Configure the event filter:
   - **Event type:** `file.ingested` (most common), `experiment.status_changed`, or `pipeline.completed`.
   - **File type filter:** For file events, specify which data types trigger the pipeline (e.g., only `fastq` files, not QC reports).
   - **Experiment filter:** Optionally restrict triggers to specific experiments or experiment templates.
5. Click "Save Trigger."

When a file matching the filter arrives, bioAF creates a pipeline run with the ingested file as input and the associated experiment's default parameters.

### Batch Triggers

Batch triggers collect qualifying events over a time window and launch a single pipeline run with all collected inputs. This is useful when a CRO delivers files incrementally but the pipeline should process them together.

To configure a batch trigger:

1. Add a trigger as above, but choose "Batch" as the trigger type.
2. Configure the batch window:
   - **Window duration:** How long to accumulate events before triggering (e.g., 2 hours, 24 hours).
   - **Minimum batch size:** The minimum number of qualifying files required to trigger. If the window closes with fewer files, the batch waits for the next window.
   - **Maximum batch size:** Cap the number of files per run to prevent resource exhaustion.
3. Configure the batch grouping:
   - **Group by experiment:** Each experiment's files are batched separately (default).
   - **Group by project:** Files from all experiments in a project are batched together.
   - **No grouping:** All qualifying files are batched into a single run.

### Scheduled Triggers

Scheduled triggers launch pipeline runs on a cron schedule regardless of whether new data has arrived. These are useful for periodic reprocessing, report generation, or Reference Dataset updates.

1. Add a trigger and choose "Scheduled" as the trigger type.
2. Enter a cron expression (e.g., `0 2 * * 1` for every Monday at 2 AM).
3. Configure the input source:
   - **Latest files:** Use the most recent files for each sample in the specified experiment.
   - **Changed since last run:** Only include files that have been added or modified since the last scheduled run.
   - **Fixed input set:** Use a specific set of files (useful for periodic re-analysis with updated references).

## Budget Pre-Flight

Every automated pipeline run passes through a budget pre-flight check before execution. This prevents runaway costs from misconfigured triggers or unexpected data volumes.

### Setting Budget Limits

Navigate to **Settings > Cost Management > Pipeline Budgets** and configure:

- **Per-run limit:** Maximum estimated cost for a single pipeline run (e.g., $50). Runs exceeding this are held for manual approval.
- **Daily limit:** Maximum total pipeline spend per 24-hour period (e.g., $500). Once reached, new runs queue but do not execute until the next day.
- **Monthly limit:** Maximum total pipeline spend per calendar month. Approaching this limit (80%, 90%) generates notifications.

### How Cost Estimation Works

bioAF estimates run costs based on:

- The pipeline's historical average resource consumption (CPU-hours, memory-hours, GPU-hours).
- The number and size of input files.
- Current GCP pricing for the selected machine types.

For pipelines with no history, bioAF uses the resource requests from the Pipeline Catalog Entry as a conservative estimate.

### When Pre-Flight Fails

If a run exceeds the per-run budget limit, bioAF:

1. Holds the run in "Pending Approval" status.
2. Sends a notification to users with the `admin` or `comp_bio` role.
3. Displays the estimated cost and the reason for the hold in the pipeline queue.

An authorized user can approve or reject the run from the queue view. Approved runs proceed immediately.

## Queue Management

All pipeline runs -- manual and automated -- enter a unified queue. The queue provides visibility and control over execution order.

### Viewing the Queue

Navigate to **Pipelines > Queue** to see all pending, running, and recently completed runs. The queue displays:

- Run ID and pipeline name.
- Trigger source (manual, event-driven, batch, scheduled).
- Estimated cost and resource requirements.
- Current status (queued, running, completed, failed, held).
- Position in queue and estimated start time.

### Prioritizing Runs

By default, runs execute in FIFO order. You can adjust priority:

- **Drag and drop:** Reorder queued runs in the UI.
- **Priority flag:** Mark a run as "High Priority" to move it ahead of normal-priority runs.
- **Preemption:** For Kubernetes backends, high-priority runs can preempt lower-priority runs if cluster resources are constrained. Enable this in **Compute > Settings > Allow Preemption**.

### Pausing and Canceling

- **Pause queue:** Click "Pause Queue" to stop new runs from starting. Running jobs continue to completion. This is useful before maintenance windows.
- **Cancel run:** Select a queued or running job and click "Cancel." For running jobs, bioAF sends a graceful termination signal and waits 30 seconds before force-killing.
- **Pause trigger:** Disable a specific trigger without deleting it. The trigger stops firing but retains its configuration.

## Monitoring Automated Runs

### Notifications

Configure notification rules in **Settings > Notifications** for automated pipeline events:

- **Run started:** Useful for awareness, typically sent to Slack.
- **Run completed:** Confirms successful processing.
- **Run failed:** Requires attention. Include the error summary in the notification body.
- **Budget threshold reached:** Early warning before hitting limits.

### Dashboard

The **Pipelines > Dashboard** view shows aggregate statistics for automated runs:

- Runs per day/week/month.
- Success rate and common failure reasons.
- Total compute cost over time.
- Average run duration by pipeline type.

## Tips

- Start with event-driven triggers for your primary alignment pipeline. Add batch and scheduled triggers as your data volume grows.
- Set conservative budget limits initially and increase them as you learn your typical per-run costs. It is much easier to approve a held run than to recover from an unexpected $2,000 bill.
- Use the "Dry Run" option when testing new triggers. Dry runs parse the trigger conditions and show what would have executed without actually launching a pipeline.
- Tag automated runs with the trigger source so you can filter them in the audit log. bioAF does this automatically, but you can add custom tags in the trigger configuration.
- If a CRO delivers corrupted files that cause pipeline failures, add a file validation step to the trigger configuration. bioAF can check file integrity (MD5, gzip validity) before passing files to the pipeline.
