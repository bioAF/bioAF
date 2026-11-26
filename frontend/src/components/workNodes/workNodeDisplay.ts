import { formatSessionStatusLabel } from "@/lib/sessionStatus";
import type { WorkNode } from "@/lib/types";

/** Plain reasons a Stop kept the node (plan 03A copy). Technical detail stays in the server logs. */
const STOP_FAILED_MESSAGES: Record<string, string> = {
  save_failed: "Your files could not be saved. The work node has been kept, so you can keep working and try again.",
  delete_unconfirmed: "Your files were saved, but bioAF could not finish stopping this work node.",
  state_unknown: "bioAF could not check this work node. Try again shortly.",
};

export function stopFailedMessage(node: WorkNode): string {
  return STOP_FAILED_MESSAGES[node.stop_failure ?? ""] ?? STOP_FAILED_MESSAGES.state_unknown;
}

/** Whether a Stop may be requested now: a running node, or a kept node (Retry stop). */
export function canRequestStop(node: WorkNode): boolean {
  return node.status === "running" || node.status === "stop_failed";
}

/** A node is connectable while running, or when a Stop kept it running because its files were not saved. */
export function isConnectable(node: WorkNode): boolean {
  return (
    !!node.access_url &&
    (node.status === "running" || (node.status === "stop_failed" && node.stop_failure === "save_failed"))
  );
}

export function workNodeStatusLabel(node: WorkNode): string {
  if (node.status === "stopping") return "Syncing outputs...";
  if (node.status === "stop_failed") return "Stop failed";
  // Prefer the backend-supplied failure taxonomy (failure_reason). Fall back
  // to the historical "no access_url means resource failure" heuristic for
  // sessions that predate the taxonomy migration.
  if (node.status === "failed" && !node.failure_reason && !node.access_url) {
    return "Resource Failure";
  }
  return formatSessionStatusLabel({ status: node.status, failure_reason: node.failure_reason });
}

export function formatUptime(startedAt: string | null): string {
  if (!startedAt) return "-";
  const diff = Date.now() - new Date(startedAt).getTime();
  const hours = Math.floor(diff / 3600000);
  const minutes = Math.floor((diff % 3600000) / 60000);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

/** access_url is like ssh://1.2.3.4:22 */
export function sshCommand(accessUrl: string | null): string {
  if (!accessUrl) return "";
  const ip = accessUrl.replace("ssh://", "").replace(/:\d+$/, "");
  return `ssh ${ip}`;
}

/** The SSH login rule a node was launched with (plan 03B), or null for nodes from before rules existed. */
export function sshLoginRuleText(node: WorkNode): string | null {
  const revision = node.ssh_policy_revision;
  if (revision === null || revision === undefined) return null;
  if (node.ssh_policy_status === "failed_closed") {
    return `Revision ${revision} could not be applied as set; this work node accepts key login only`;
  }
  if (revision === 0) return "No range: password or key from anywhere";
  if (!node.ssh_policy_status) return `Revision ${revision} (not reported by the work node yet)`;
  if (node.ssh_policy_status === "failed") return `Revision ${revision} could not be checked on this work node`;
  return `Revision ${revision}, in force on this work node`;
}
