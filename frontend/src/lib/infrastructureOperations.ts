/**
 * Infrastructure operations: types and the user-facing copy signed off in mockups M2-M10.
 *
 * One accepted operation holds installation-wide exclusion; every admin sees the same operation,
 * and conflicting controls are unavailable for everyone until it settles. Copy lives here so the
 * banner, tooltips, status popup and conflict dialog say the same thing.
 */

import type { components } from "../../contracts/internal-api";

export type OperationView = components["schemas"]["OperationView"];
export type OperationStep = components["schemas"]["OperationStep"];
export type ActiveOperation = components["schemas"]["ActiveOperationResponse"];

/** How each action is named mid-sentence ("Closing this window does not stop the teardown."). */
const ACTION_NOUNS: Record<string, string> = {
  compute_deploy: "deployment",
  compute_teardown: "teardown",
  storage_deploy: "storage deployment",
  storage_update: "storage update",
  storage_destroy: "storage destroy",
  infrastructure_update: "infrastructure update",
  cluster_config: "cluster settings change",
  image_build: "image build",
  notebook_image_build: "image build",
  cellxgene_image_build: "image build",
  foundation_bootstrap: "initialization",
  billing_export_enable: "billing export setup",
  billing_export_teardown: "billing export removal",
  terraform_plan: "plan",
  terraform_apply: "apply",
  config_sync: "configuration sync",
};

export function actionNoun(op: Pick<OperationView, "action" | "label">): string {
  return ACTION_NOUNS[op.action] ?? op.label.toLowerCase();
}

export function shortTime(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** A time, with its date when it is not today (for example "Nov 22, 09:15"). */
export function timeWithDay(iso: string | null | undefined): string {
  if (!iso) return "";
  const when = new Date(iso);
  const time = shortTime(iso);
  if (when.toDateString() === new Date().toDateString()) return time;
  return `${when.toLocaleDateString(undefined, { month: "short", day: "numeric" })}, ${time}`;
}

const ACTIVE = new Set(["accepted", "running", "stopping"]);

export function isActive(op: Pick<OperationView, "status">): boolean {
  return ACTIVE.has(op.status) || op.status === "recovery_required";
}

/**
 * The shared banner (M3, M4, M9, M10). `active` is null when the status could not be confirmed.
 * Returns null when there is nothing to announce.
 */
export function bannerText(active: ActiveOperation | null, isAdmin: boolean): string | null {
  if (active === null) {
    return isAdmin
      ? "bioAF cannot confirm whether an infrastructure action is running. Actions stay unavailable until it can. bioAF keeps checking."
      : null;
  }
  if (active.busy) {
    const op = active.operation;
    if (!isAdmin || !op) {
      return active.state === "recovery_required"
        ? "Infrastructure maintenance is paused. Pipeline runs, notebooks, and uploads may be unavailable until an administrator finishes it."
        : "Infrastructure maintenance in progress. Pipeline runs, notebooks, and uploads may be unavailable until it finishes.";
    }
    if (op.status === "recovery_required") {
      return `Infrastructure actions are paused. ${op.label} (Operation #${op.id}) needs recovery before anything else can run.`;
    }
    const who = op.started_by ? `, started by ${op.started_by}` : "";
    return `${op.label} in progress${who} at ${shortTime(op.accepted_at)}. Other infrastructure actions are unavailable until it finishes.`;
  }
  const interrupted = isAdmin ? active.interrupted?.[0] : undefined;
  if (interrupted) {
    return `${interrupted.label} was interrupted by a server restart at ${shortTime(interrupted.finished_at)}.`;
  }
  return null;
}

/** Why a conflicting infrastructure control is unavailable (M3), or null when it is not. */
export function blockedReason(active: ActiveOperation | null): string | null {
  if (active === null) return "Unavailable until bioAF can confirm no other infrastructure action is running.";
  if (!active.busy) return null;
  const op = active.operation;
  if (!op) return "Unavailable while another infrastructure action runs.";
  return `Unavailable while ${op.label} (Operation #${op.id}) runs.`;
}

/** The status popup title (M2, M7, M8, M9, M10, M16). */
export function operationTitle(op: OperationView): string {
  switch (op.status) {
    case "failed":
      if (op.action === "storage_destroy" && op.error_category === "uploads_in_progress") {
        return "Storage destroy stopped before deleting anything";
      }
      return `${op.label} failed`;
    case "interrupted":
      return `${op.label} interrupted`;
    case "recovery_required":
      return `${op.label} needs recovery`;
    case "cancelled":
      return `${op.label} cancelled`;
    case "succeeded":
      return op.action === "storage_destroy" ? "Storage destroyed" : op.label;
    default:
      return op.label;
  }
}

const STEP_STATES: Record<string, string> = {
  done: "Done",
  waiting: "Waiting",
  failed: "Failed",
  skipped: "Skipped",
  already_removed: "Already removed",
  interrupted: "Interrupted",
  not_started: "Not started",
  stopped: "Stopped",
};

export function stepStateText(step: OperationStep): string {
  if (step.state === "running") {
    if (step.total) return `${step.completed ?? 0} of ${step.total}`;
    return "Running";
  }
  return STEP_STATES[step.state] ?? step.state;
}

/** The popup footer hint (M2 for the admin who started it, M5 when opened from the banner). */
export function footerHint(op: OperationView, { openedFromBanner }: { openedFromBanner: boolean }): string | null {
  if (!isActive(op) || op.status === "recovery_required") return null;
  if (openedFromBanner) return "Opened from the banner. Started by another administrator.";
  return `Closing this window does not stop the ${actionNoun(op)}.`;
}

/** The refusal shown when another action started first (M6). */
export function conflictCopy(op: OperationView, requested: string) {
  const noun = actionNoun(op);
  const who = op.started_by ? ` by ${op.started_by}` : "";
  return {
    title: "Another infrastructure action started first",
    body: `${op.label} (Operation #${op.id}) was started${who} at ${shortTime(op.accepted_at)}. Your ${requested} was not started.`,
    secondary: `You can follow the ${noun}'s progress and try again after it finishes.`,
    view: `View ${noun} status`,
  };
}
