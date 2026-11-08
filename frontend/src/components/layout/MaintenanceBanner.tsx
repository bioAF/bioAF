"use client";

import type { MaintenanceInfo } from "@/lib/maintenance";
import { maintenanceMessage } from "@/components/layout/maintenanceMessage";

/**
 * Shown above the page, not instead of it, when the backend goes into maintenance while someone is
 * working (plan 9). The page stays mounted so nothing typed is lost; requests fail with a plain
 * message until the upgrade is done, and the banner goes away by itself when it is.
 */
export function MaintenanceBanner({ info }: { info: MaintenanceInfo }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Upgrade in progress"
      className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900"
    >
      {maintenanceMessage(info, "working")}
    </div>
  );
}
