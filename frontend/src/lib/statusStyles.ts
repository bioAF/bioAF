/**
 * Single source of truth for status presentation (badge colors, dot colors,
 * labels), keyed by entity then status value.
 *
 * Why keyed by entity: the same status string means different things across
 * entities and must render differently. A Pipeline Run that is `running` is
 * blue; a Compute Session that is `running` is green. A flat status->color map
 * (the old per-page `STATUS_COLORS` constants) cannot express that and drifts
 * over time. Look colors/labels up through this module instead of hand-rolling
 * a local map.
 *
 * Add a new entity by adding a key to STATUS_STYLES. Add a new status by adding
 * an entry to the relevant entity. `dot` is only needed for entities rendered
 * as a colored dot (health/version indicators); `label` is optional and
 * defaults to the humanized status (underscores -> spaces).
 */

export interface StatusStyle {
  /** Tailwind classes for a text/background badge pill. */
  badge: string;
  /** Tailwind classes for a small colored dot indicator (optional). */
  dot?: string;
  /** Human-readable label (optional; defaults to the humanized status). */
  label?: string;
}

type StatusDomain = Record<string, StatusStyle>;

const NEUTRAL_BADGE = "bg-gray-100 text-gray-600";
const NEUTRAL_DOT = "bg-gray-400";

function humanize(status: string): string {
  return status.replace(/_/g, " ");
}

export const STATUS_STYLES: Record<string, StatusDomain> = {
  // Experiment: the fixed lifecycle (registered -> ... -> complete) plus the
  // higher-level summary statuses used by the dashboard widget.
  //
  // The lifecycle is ONE linear progression, so colour encodes *how far along* it
  // is, not *which step* it is. It previously used nine unrelated hues (gray, blue,
  // indigo, purple, yellow, teal, cyan, orange, green), which made colour an
  // arbitrary identifier that could not be learned, and put `analysis` in orange
  // directly beside `failed` red so a normal step read as a warning.
  //
  // The ramp is: neutral (not started) -> brand-light (sample in the lab) ->
  // brand-deep (compute and interpretation) -> green (done). Red is reserved for
  // failure and never appears in the lifecycle. The exact step is carried by the
  // label, which is always rendered with the badge.
  //
  // Both brand steps stay within the set of utilities the dark-mode override layer
  // in globals.css remaps (bg-bioaf-50/100, text-bioaf-600/700/800). Going deeper
  // (bioaf-200, text-bioaf-900) would render untreated on the dark canvas.
  experiment: {
    registered: { badge: "bg-gray-100 text-gray-800", label: "Registered" },
    library_prep: { badge: "bg-bioaf-50 text-bioaf-700", label: "Library Prep" },
    sequencing: { badge: "bg-bioaf-50 text-bioaf-700", label: "Sequencing" },
    fastq_uploaded: { badge: "bg-bioaf-50 text-bioaf-700", label: "FASTQ Uploaded" },
    processing: { badge: "bg-bioaf-100 text-bioaf-800", label: "Processing" },
    pipeline_complete: { badge: "bg-bioaf-100 text-bioaf-800", label: "Pipeline Complete" },
    reviewed: { badge: "bg-bioaf-100 text-bioaf-800", label: "Reviewed" },
    analysis: { badge: "bg-bioaf-100 text-bioaf-800", label: "Analysis" },
    complete: { badge: "bg-green-100 text-green-800", label: "Complete" },
    // Dashboard widget summary statuses (distinct keys, do not collide above).
    active: { badge: "bg-green-100 text-green-700", label: "Active" },
    in_progress: { badge: "bg-green-100 text-green-700", label: "In Progress" },
    completed: { badge: "bg-blue-100 text-blue-700", label: "Completed" },
    draft: { badge: "bg-gray-100 text-gray-600", label: "Draft" },
    planned: { badge: "bg-gray-100 text-gray-600", label: "Planned" },
    archived: { badge: "bg-gray-100 text-gray-500", label: "Archived" },
    failed: { badge: "bg-red-100 text-red-700", label: "Failed" },
    cancelled: { badge: "bg-red-100 text-red-700", label: "Cancelled" },
  },

  // Pipeline Run / Pipeline Process status.
  pipelineRun: {
    pending: { badge: "bg-gray-100 text-gray-700" },
    running: { badge: "bg-blue-100 text-blue-700" },
    completed: { badge: "bg-green-100 text-green-700" },
    failed: { badge: "bg-red-100 text-red-700" },
    cancelled: { badge: "bg-orange-100 text-orange-700" },
    cached: { badge: "bg-purple-100 text-purple-700" },
  },

  // Environment Version build status (rendered as both a badge and a dot).
  environmentVersion: {
    draft: { badge: "bg-gray-100 text-gray-700", dot: "bg-gray-400", label: "Draft" },
    building: { badge: "bg-yellow-100 text-yellow-700", dot: "bg-yellow-500", label: "Building" },
    ready: { badge: "bg-green-100 text-green-700", dot: "bg-green-500", label: "Ready" },
    failed: { badge: "bg-red-100 text-red-700", dot: "bg-red-500", label: "Failed" },
  },

  // Compute Session (Notebook Session + Work Node) lifecycle.
  computeSession: {
    pending: { badge: "bg-gray-100 text-gray-800" },
    starting: { badge: "bg-blue-100 text-blue-800" },
    running: { badge: "bg-green-100 text-green-800" },
    idle: { badge: "bg-yellow-100 text-yellow-800" },
    stopping: { badge: "bg-orange-100 text-orange-800" },
    stopped: { badge: "bg-gray-100 text-gray-600" },
    failed: { badge: "bg-red-100 text-red-800" },
  },

  // Sample Batch ingestion status.
  sampleBatch: {
    pending: { badge: "bg-gray-100 text-gray-700" },
    ingesting: { badge: "bg-blue-100 text-blue-700" },
    complete: { badge: "bg-green-100 text-green-700" },
    partial_complete: { badge: "bg-yellow-100 text-yellow-700" },
    failed: { badge: "bg-red-100 text-red-700" },
  },

  // Reference Dataset lifecycle (custom labels).
  referenceDataset: {
    active: { badge: "bg-green-100 text-green-800", label: "Active" },
    deprecated: { badge: "bg-red-100 text-red-800 line-through", label: "Deprecated" },
    pending_approval: { badge: "bg-yellow-100 text-yellow-800", label: "Pending Approval" },
    uploading: { badge: "bg-blue-100 text-blue-800", label: "Importing" },
    failed: { badge: "bg-red-100 text-red-800", label: "Failed" },
  },

  // Terraform run status (uses the text-700/bg-50 shade convention).
  terraformRun: {
    completed: { badge: "text-green-700 bg-green-50" },
    failed: { badge: "text-red-700 bg-red-50" },
    planning: { badge: "text-blue-700 bg-blue-50" },
    applying: { badge: "text-blue-700 bg-blue-50" },
    awaiting_confirmation: { badge: "text-amber-700 bg-amber-50" },
    cancelled: { badge: "text-gray-700 bg-gray-50" },
  },

  // Orphaned cloud resource cleanup lifecycle (text-700/bg-50 shades).
  orphanedResource: {
    detected: { badge: "text-amber-700 bg-amber-50" },
    cleaning: { badge: "text-blue-700 bg-blue-50" },
    cleaned: { badge: "text-green-700 bg-green-50" },
    dismissed: { badge: "text-gray-700 bg-gray-50" },
    failed: { badge: "text-red-700 bg-red-50" },
  },

  // Service / infrastructure health (rendered as a colored dot).
  serviceHealth: {
    healthy: { badge: "bg-green-100 text-green-700", dot: "bg-green-400" },
    degraded: { badge: "bg-yellow-100 text-yellow-700", dot: "bg-yellow-400" },
    unhealthy: { badge: "bg-red-100 text-red-700", dot: "bg-red-400" },
    unknown: { badge: "bg-gray-100 text-gray-600", dot: "bg-gray-400" },
  },

  // Backup tier health.
  backupTier: {
    healthy: { badge: "bg-green-100 text-green-700" },
    warning: { badge: "bg-yellow-100 text-yellow-700" },
    error: { badge: "bg-red-100 text-red-700" },
    unknown: { badge: "bg-gray-100 text-gray-600" },
  },

  // Scientific Decision Record status.
  sdr: {
    draft: { badge: "bg-gray-100 text-gray-700", label: "Draft" },
    active: { badge: "bg-green-100 text-green-800", label: "Active" },
    flagged_for_review: { badge: "bg-amber-100 text-amber-800", label: "Flagged for Review" },
    superseded: { badge: "bg-gray-100 text-gray-400 line-through", label: "Superseded" },
    repealed: { badge: "bg-gray-100 text-gray-400 line-through", label: "Repealed" },
  },

  // GEO export field-validation segments (solid swatch colors for the bar/legend).
  geoValidation: {
    complete: { badge: "bg-green-500", label: "Complete" },
    populated_unvalidated: { badge: "bg-yellow-500", label: "Unvalidated" },
    missing_required: { badge: "bg-red-500", label: "Missing (Required)" },
    missing_recommended: { badge: "bg-gray-400", label: "Missing (Recommended)" },
  },

  // Literature Paper provenance (how a paper entered the Library). Labels live here
  // rather than in the page: they used to be a local PROVENANCE_LABELS map in
  // literature/page.tsx, so the paper *detail* page had no access to them and
  // rendered the raw enum ("user_upload") straight at the user.
  literatureProvenance: {
    user_upload: { badge: "bg-blue-100 text-blue-800", label: "Uploaded" },
    source_search: { badge: "bg-green-100 text-green-800", label: "From search" },
    lit_review_run: { badge: "bg-purple-100 text-purple-800", label: "AI Lit Review" },
  },

  // Literature Paper full-text extraction state. Values mirror the backend
  // constants in backend/app/models/literature.py:35-38 (EXTRACTION_NONE /
  // PENDING / COMPLETE / FAILED). The detail page rendered this column raw, so a
  // paper with no PDF reported its extraction status as the bare word "none".
  literatureExtraction: {
    none: { badge: "bg-gray-100 text-gray-600", label: "Not extracted" },
    pending: { badge: "bg-yellow-100 text-yellow-800", label: "Pending" },
    complete: { badge: "bg-green-100 text-green-800", label: "Extracted" },
    failed: { badge: "bg-red-100 text-red-800", label: "Failed" },
  },

  // Literature Paper reading status. Labels live here for the same reason as
  // provenance above: the detail page rendered the bare lowercase enum.
  literatureReading: {
    unread: { badge: "bg-gray-100 text-gray-700", label: "Unread" },
    reading: { badge: "bg-amber-100 text-amber-800", label: "Reading" },
    read: { badge: "bg-emerald-100 text-emerald-800", label: "Read" },
  },

  // Literature recommendation relevance bucket (color only).
  recommendationBucket: {
    high: { badge: "bg-green-100 text-green-800" },
    medium: { badge: "bg-yellow-100 text-yellow-800" },
    low: { badge: "bg-gray-100 text-gray-700" },
  },

  // Literature Validation outcome tone (positive/caution/negative/neutral). The
  // tone itself is derived in lib/validationStatus + lib/validationClassification;
  // this is the shared palette those tones map to.
  validationTone: {
    positive: { badge: "bg-green-100 text-green-800" },
    caution: { badge: "bg-yellow-100 text-yellow-800" },
    negative: { badge: "bg-red-100 text-red-800" },
    neutral: { badge: "bg-gray-100 text-gray-700" },
  },

  // Literature Validation pipeline-stage kind, shown while a study is in progress
  // (before it reaches a classification). Keyed by ValidationStageKind
  // (lib/validationStage). `classified` renders a validationTone badge instead.
  validationStage: {
    in_progress: { badge: "bg-blue-100 text-blue-800" },
    awaiting_review: { badge: "bg-yellow-100 text-yellow-800" },
    declined: { badge: "bg-gray-100 text-gray-700" },
    error: { badge: "bg-red-100 text-red-800" },
    classified: { badge: "bg-gray-100 text-gray-700" },
  },

  // Sample QC status.
  sampleQc: {
    pass: { badge: "bg-green-100 text-green-800", label: "Pass" },
    warning: { badge: "bg-yellow-100 text-yellow-800", label: "Warning" },
    fail: { badge: "bg-red-100 text-red-800", label: "Fail" },
  },

  // Scientific review verdict.
  review: {
    approved: { badge: "bg-green-100 text-green-800", label: "Approved" },
    approved_with_caveats: { badge: "bg-yellow-100 text-yellow-800", label: "Approved w/ Caveats" },
    rejected: { badge: "bg-red-100 text-red-800", label: "Rejected" },
    revision_requested: { badge: "bg-orange-100 text-orange-800", label: "Revision Requested" },
  },

  // QC quality_rating pill (excellent/good/acceptable/pending_review). An unknown
  // rating renders red at the call site (a data error should read as alarming),
  // so it is deliberately NOT listed here as a neutral fallback.
  qcQuality: {
    excellent: { badge: "bg-green-100 text-green-700" },
    good: { badge: "bg-blue-100 text-blue-700" },
    acceptable: { badge: "bg-yellow-100 text-yellow-700" },
    pending_review: { badge: "bg-gray-100 text-gray-700" },
  },

  // What changed between two versions of a Custom Pipeline. The launch dialog
  // and the detail page both label version history, and each carried its own
  // byte-identical copy of the mapping before this entry.
  pipelineVersionChange: {
    initial: { badge: "bg-gray-100 text-gray-700", label: "Initial version" },
    image: { badge: "bg-amber-100 text-amber-700", label: "Image change" },
    config_and_image: { badge: "bg-purple-100 text-purple-700", label: "Config + image change" },
    config: { badge: "bg-blue-100 text-blue-700", label: "Config change" },
  },

  // Event severity, shared by notifications and the activity feed. They are two
  // views of the same scale, so they must not diverge: before this entry the
  // notification list painted `warning` yellow and the activity feed painted it
  // amber, on screens a user moves between. Yellow wins because it is what the
  // rest of this registry already uses for a warning (environmentVersion
  // building, sampleBatch partial_complete) and because yellow-700 on yellow-100
  // measures 4.58:1, slightly ahead of amber's 4.51:1.
  //
  // Dots are the -500 step, matching environmentVersion, rather than the -400
  // the activity widget used. Note that none of these dots reaches 3:1 on white:
  // a dot is not a sufficient carrier for severity on its own, which is a
  // separate open finding and not fixed by this entry.
  severity: {
    info: { badge: "bg-blue-100 text-blue-700", dot: "bg-blue-500", label: "Info" },
    warning: { badge: "bg-yellow-100 text-yellow-700", dot: "bg-yellow-500", label: "Warning" },
    critical: { badge: "bg-red-100 text-red-700", dot: "bg-red-500", label: "Critical" },
  },

  // Catch-all for mixed/generic status surfaces (component health, users,
  // projects, publications). Labels default to the humanized status.
  generic: {
    healthy: { badge: "bg-green-100 text-green-800" },
    running: { badge: "bg-green-100 text-green-800" },
    active: { badge: "bg-green-100 text-green-800" },
    completed: { badge: "bg-green-100 text-green-800" },
    disabled: { badge: "bg-gray-100 text-gray-600" },
    provisioning: { badge: "bg-yellow-100 text-yellow-800" },
    applying: { badge: "bg-yellow-100 text-yellow-800" },
    pending: { badge: "bg-yellow-100 text-yellow-800" },
    planning: { badge: "bg-yellow-100 text-yellow-800" },
    awaiting_confirmation: { badge: "bg-blue-100 text-blue-800" },
    destroying: { badge: "bg-orange-100 text-orange-800" },
    error: { badge: "bg-red-100 text-red-800" },
    failed: { badge: "bg-red-100 text-red-800" },
    unhealthy: { badge: "bg-red-100 text-red-800" },
    degraded: { badge: "bg-yellow-100 text-yellow-800" },
    invited: { badge: "bg-blue-100 text-blue-800" },
    deactivated: { badge: "bg-gray-100 text-gray-600" },
    cancelled: { badge: "bg-gray-100 text-gray-600" },
  },
};

/** Resolve the full style for an (entity, status), with neutral fallbacks. */
export function statusStyle(entity: string, status: string): Required<Pick<StatusStyle, "badge" | "label">> & { dot: string } {
  const entry = STATUS_STYLES[entity]?.[status];
  return {
    badge: entry?.badge ?? NEUTRAL_BADGE,
    dot: entry?.dot ?? NEUTRAL_DOT,
    label: entry?.label ?? humanize(status),
  };
}

/** Tailwind classes for a badge pill. */
export function statusBadgeClass(entity: string, status: string): string {
  return STATUS_STYLES[entity]?.[status]?.badge ?? NEUTRAL_BADGE;
}

/** Tailwind classes for a colored dot indicator. */
export function statusDotClass(entity: string, status: string): string {
  return STATUS_STYLES[entity]?.[status]?.dot ?? NEUTRAL_DOT;
}

/** Human-readable label, defaulting to the humanized status. */
export function statusLabel(entity: string, status: string): string {
  return STATUS_STYLES[entity]?.[status]?.label ?? humanize(status);
}
