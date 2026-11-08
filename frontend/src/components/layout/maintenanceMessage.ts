import type { MaintenanceInfo } from "@/lib/maintenance";

/**
 * The plain sentence for a backend in maintenance (plan 9). The reason comes from the backend's
 * upgrade gate; technical detail (step, error, file paths) stays in the server's record and logs.
 */
export function maintenanceMessage(info: MaintenanceInfo, when: "booting" | "working"): string {
  if (info.reason === "upgrade_failed") {
    return "bioAF's last upgrade did not finish, so it is not available yet. An administrator can see why on the server and retry it.";
  }
  if (when === "working") {
    return "bioAF is finishing an upgrade. This page is kept as it is, but changes cannot be saved until the upgrade is done. This message goes away by itself.";
  }
  return "bioAF is finishing an upgrade. This page opens by itself when it is done.";
}
