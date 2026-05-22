"use client";

// A single QC dashboard list row, with the run, pipeline, project, experiment,
// samples, generated date, and quality badge. Shared so the Results > QC
// Dashboards page and the Experiment Results tab render the same card.

import { QualityBadge } from "./QualityBadge";
import type { QCDashboardSummary } from "@/lib/types";

export function QCDashboardListItem({
  dashboard,
  onClick,
}: {
  dashboard: QCDashboardSummary;
  onClick: () => void;
}) {
  const samples = dashboard.sample_external_ids ?? [];
  const sampleDisplay =
    samples.length === 0
      ? null
      : samples.length <= 3
        ? samples.join(", ")
        : `${samples.slice(0, 3).join(", ")} +${samples.length - 3} more`;

  return (
    <div
      onClick={onClick}
      className="p-4 flex items-start justify-between gap-4 hover:bg-gray-50 cursor-pointer"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 flex-wrap">
          <p className="font-medium text-sm">Run #{dashboard.pipeline_run_id}</p>
          {dashboard.pipeline_name && (
            <span className="text-xs text-gray-600">
              {`${dashboard.pipeline_name}${dashboard.pipeline_version ? ` v${dashboard.pipeline_version}` : ""}`}
            </span>
          )}
        </div>
        <div className="mt-1 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1 text-xs text-gray-600">
          {dashboard.project_name && (
            <div>
              <span className="text-gray-400">Project:</span>{" "}
              <span className="text-gray-700">{dashboard.project_name}</span>
            </div>
          )}
          {dashboard.experiment_name && (
            <div>
              <span className="text-gray-400">Experiment:</span>{" "}
              <span className="text-gray-700">{dashboard.experiment_name}</span>
            </div>
          )}
          {sampleDisplay && (
            <div className="sm:col-span-2 lg:col-span-1">
              <span className="text-gray-400">Samples:</span>{" "}
              <span className="text-gray-700">{sampleDisplay}</span>
            </div>
          )}
        </div>
        <p className="text-xs text-gray-400 mt-1">
          Generated{" "}
          {dashboard.generated_at ? new Date(dashboard.generated_at).toLocaleDateString() : "N/A"}
          {dashboard.cell_count != null && ` | ${dashboard.cell_count.toLocaleString()} cells`}
        </p>
      </div>
      <QualityBadge rating={dashboard.quality_rating} />
    </div>
  );
}
