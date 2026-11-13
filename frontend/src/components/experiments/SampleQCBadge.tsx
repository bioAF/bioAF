"use client";

import { statusBadgeClass, statusLabel } from "@/lib/statusStyles";

// An open wire value: an unknown QC status gets a neutral badge and a humanized label.
export function SampleQCBadge({ status }: { status: string | null }) {
  if (!status) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600">
        Not Set
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${statusBadgeClass("sampleQc", status)}`}>
      {statusLabel("sampleQc", status)}
    </span>
  );
}
