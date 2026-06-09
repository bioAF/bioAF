"use client";

import type { ExperimentStatus } from "@/lib/types";
import { statusBadgeClass, statusLabel } from "@/lib/statusStyles";

export function ExperimentStatusBadge({ status }: { status: ExperimentStatus }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${statusBadgeClass("experiment", status)}`}>
      {statusLabel("experiment", status)}
    </span>
  );
}
