import { formatSessionStatusLabel } from "@/lib/sessionStatus";
import type { WorkNode } from "@/lib/types";

export function workNodeStatusLabel(node: WorkNode): string {
  if (node.status === "stopping") return "Syncing outputs...";
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
