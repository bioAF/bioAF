"use client";

import type { ReviewVerdict } from "@/lib/types";
import { statusBadgeClass, statusLabel } from "@/lib/statusStyles";

interface ReviewBadgeProps {
  verdict: ReviewVerdict;
  size?: "sm" | "md";
}

export function ReviewBadge({ verdict, size = "sm" }: ReviewBadgeProps) {
  const sizeClass = size === "md" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs";
  return (
    <span className={`inline-flex items-center rounded font-medium ${sizeClass} ${statusBadgeClass("review", verdict)}`}>
      {statusLabel("review", verdict)}
    </span>
  );
}
