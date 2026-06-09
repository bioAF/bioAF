"use client";

import { statusBadgeClass, statusLabel } from "@/lib/statusStyles";

interface ReferenceStatusBadgeProps {
  status: string;
  size?: "sm" | "md";
}

export function ReferenceStatusBadge({ status, size = "sm" }: ReferenceStatusBadgeProps) {
  const sizeClass = size === "md" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs";
  return (
    <span className={`inline-flex items-center rounded font-medium ${sizeClass} ${statusBadgeClass("referenceDataset", status)}`}>
      {statusLabel("referenceDataset", status)}
    </span>
  );
}
