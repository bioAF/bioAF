"use client";

import type { QCStatus } from "@/lib/types";
import { statusBadgeClass, statusLabel } from "@/lib/statusStyles";

export function SampleQCBadge({ status }: { status: QCStatus | null }) {
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
