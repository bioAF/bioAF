"use client";

const statusConfig: Record<string, { label: string; className: string }> = {
  active: { label: "Active", className: "bg-green-100 text-green-800" },
  deprecated: { label: "Deprecated", className: "bg-red-100 text-red-800 line-through" },
  pending_approval: { label: "Pending Approval", className: "bg-yellow-100 text-yellow-800" },
  uploading: { label: "Importing", className: "bg-blue-100 text-blue-800" },
  failed: { label: "Failed", className: "bg-red-100 text-red-800" },
};

interface ReferenceStatusBadgeProps {
  status: string;
  size?: "sm" | "md";
}

export function ReferenceStatusBadge({ status, size = "sm" }: ReferenceStatusBadgeProps) {
  const config = statusConfig[status] || { label: status, className: "bg-gray-100 text-gray-600" };
  const sizeClass = size === "md" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs";
  return (
    <span className={`inline-flex items-center rounded font-medium ${sizeClass} ${config.className}`}>
      {config.label}
    </span>
  );
}
