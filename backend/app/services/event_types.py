"""Event type string constants for the bioAF notification system."""

# Pipeline events
PIPELINE_COMPLETED = "pipeline.completed"
PIPELINE_FAILED = "pipeline.failed"
PIPELINE_STAGE_ERROR = "pipeline.stage_error"
PIPELINE_RUN_REVIEWED = "pipeline_run.reviewed"
PIPELINE_RUN_REVIEW_REMINDER = "pipeline_run.review_reminder"

# QC events
QC_RESULTS_READY = "qc.results_ready"

# Experiment events
EXPERIMENT_STATUS_CHANGED = "experiment.status_changed"

# Budget events
BUDGET_THRESHOLD_50 = "budget.threshold_50"
BUDGET_THRESHOLD_80 = "budget.threshold_80"
BUDGET_THRESHOLD_100 = "budget.threshold_100"

# Compute events
COMPUTE_NODE_FAILURE = "compute.node_failure"

# Component health events
COMPONENT_HEALTH_DEGRADED = "component.health_degraded"
COMPONENT_HEALTH_DOWN = "component.health_down"

# Backup events
BACKUP_FAILURE = "backup.failure"

# Quota events
QUOTA_WARNING = "quota.warning"

# Session events
SESSION_IDLE = "session.idle"

# Work node events
WORK_NODE_LAUNCHED = "work_node.launched"
WORK_NODE_STOPPED = "work_node.stopped"
WORK_NODE_HEARTBEAT_TIMEOUT = "work_node.heartbeat_timeout"

# Results events
RESULTS_PUBLISHED = "results.published"

# Data events
DATA_UPLOADED = "data.uploaded"

# Platform events
PLATFORM_UPDATE_AVAILABLE = "platform.update_available"

# Storage events
STORAGE_THRESHOLD = "storage.threshold"

# User events
USER_INVITATION_ACCEPTED = "user.invitation_accepted"

# Reference data events
REFERENCE_DEPRECATED = "reference.deprecated"

# Ingest events
FILES_CATALOGED = "ingest.files_cataloged"
UNCLAIMED_ENTITY = "ingest.unclaimed_entity"
UNMATCHED_FILE = "ingest.unmatched_file"
DUPLICATE_FILE = "ingest.duplicate_file"
INGEST_FAILURE = "ingest.failure"
INGEST_BATCH_COMPLETE = "ingest.batch_complete"

# Sequencing batch events
SEQUENCING_BATCH_DETECTED = "sequencing_batch.detected"
SEQUENCING_BATCH_FILE_VERIFIED = "sequencing_batch.file_verified"
SEQUENCING_BATCH_COMPLETE = "sequencing_batch.complete"
SEQUENCING_BATCH_PARTIAL = "sequencing_batch.partial"

# Trigger events
AUTO_RUN_SUBMITTED = "trigger.auto_run_submitted"
RUN_QUEUED_BUDGET = "trigger.run_queued_budget"
RUN_QUEUED_EXHAUSTED = "trigger.run_queued_exhausted"
BUDGET_MID_QUEUE = "trigger.budget_mid_queue"
EVALUATION_FAILED = "trigger.evaluation_failed"
BATCH_WINDOW_CLOSED = "trigger.batch_window_closed"

# Auto-run events
AUTO_RUN_BUDGET_DISABLED = "auto_run.budget_disabled"
AUTO_RUN_LAUNCHED = "auto_run.launched"
AUTO_RUN_CANCELLED = "auto_run.cancelled"

# Terraform events
TERRAFORM_APPLY_FAILURE = "terraform.apply_failure"

ALL_EVENT_TYPES = [
    PIPELINE_COMPLETED,
    PIPELINE_FAILED,
    PIPELINE_STAGE_ERROR,
    QC_RESULTS_READY,
    EXPERIMENT_STATUS_CHANGED,
    BUDGET_THRESHOLD_50,
    BUDGET_THRESHOLD_80,
    BUDGET_THRESHOLD_100,
    COMPUTE_NODE_FAILURE,
    COMPONENT_HEALTH_DEGRADED,
    COMPONENT_HEALTH_DOWN,
    BACKUP_FAILURE,
    QUOTA_WARNING,
    SESSION_IDLE,
    RESULTS_PUBLISHED,
    DATA_UPLOADED,
    PLATFORM_UPDATE_AVAILABLE,
    STORAGE_THRESHOLD,
    USER_INVITATION_ACCEPTED,
    TERRAFORM_APPLY_FAILURE,
    PIPELINE_RUN_REVIEWED,
    PIPELINE_RUN_REVIEW_REMINDER,
    REFERENCE_DEPRECATED,
    FILES_CATALOGED,
    UNCLAIMED_ENTITY,
    UNMATCHED_FILE,
    DUPLICATE_FILE,
    INGEST_FAILURE,
    INGEST_BATCH_COMPLETE,
    AUTO_RUN_SUBMITTED,
    RUN_QUEUED_BUDGET,
    RUN_QUEUED_EXHAUSTED,
    BUDGET_MID_QUEUE,
    EVALUATION_FAILED,
    BATCH_WINDOW_CLOSED,
    WORK_NODE_LAUNCHED,
    WORK_NODE_STOPPED,
    WORK_NODE_HEARTBEAT_TIMEOUT,
    SEQUENCING_BATCH_DETECTED,
    SEQUENCING_BATCH_FILE_VERIFIED,
    SEQUENCING_BATCH_COMPLETE,
    SEQUENCING_BATCH_PARTIAL,
    AUTO_RUN_BUDGET_DISABLED,
    AUTO_RUN_LAUNCHED,
    AUTO_RUN_CANCELLED,
]

# Severity mapping for event types
EVENT_SEVERITY = {
    PIPELINE_COMPLETED: "info",
    PIPELINE_FAILED: "critical",
    PIPELINE_STAGE_ERROR: "warning",
    QC_RESULTS_READY: "info",
    EXPERIMENT_STATUS_CHANGED: "info",
    BUDGET_THRESHOLD_50: "info",
    BUDGET_THRESHOLD_80: "warning",
    BUDGET_THRESHOLD_100: "critical",
    COMPUTE_NODE_FAILURE: "critical",
    COMPONENT_HEALTH_DEGRADED: "warning",
    COMPONENT_HEALTH_DOWN: "critical",
    BACKUP_FAILURE: "critical",
    QUOTA_WARNING: "warning",
    SESSION_IDLE: "info",
    RESULTS_PUBLISHED: "info",
    DATA_UPLOADED: "info",
    PLATFORM_UPDATE_AVAILABLE: "info",
    STORAGE_THRESHOLD: "warning",
    USER_INVITATION_ACCEPTED: "info",
    TERRAFORM_APPLY_FAILURE: "critical",
    PIPELINE_RUN_REVIEWED: "info",
    PIPELINE_RUN_REVIEW_REMINDER: "warning",
    REFERENCE_DEPRECATED: "warning",
    FILES_CATALOGED: "info",
    UNCLAIMED_ENTITY: "warning",
    UNMATCHED_FILE: "warning",
    DUPLICATE_FILE: "info",
    INGEST_FAILURE: "critical",
    INGEST_BATCH_COMPLETE: "info",
    AUTO_RUN_SUBMITTED: "info",
    RUN_QUEUED_BUDGET: "warning",
    RUN_QUEUED_EXHAUSTED: "critical",
    BUDGET_MID_QUEUE: "warning",
    EVALUATION_FAILED: "critical",
    BATCH_WINDOW_CLOSED: "info",
    WORK_NODE_LAUNCHED: "info",
    WORK_NODE_STOPPED: "info",
    WORK_NODE_HEARTBEAT_TIMEOUT: "warning",
    SEQUENCING_BATCH_DETECTED: "info",
    SEQUENCING_BATCH_FILE_VERIFIED: "info",
    SEQUENCING_BATCH_COMPLETE: "info",
    SEQUENCING_BATCH_PARTIAL: "warning",
    AUTO_RUN_BUDGET_DISABLED: "critical",
    AUTO_RUN_LAUNCHED: "info",
    AUTO_RUN_CANCELLED: "warning",
}
