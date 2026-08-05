"use client";

// The real QC report shown in a backdrop-closeable modal. Used by the
// Experiment Results tab so it renders the same QC dashboard (and AI Review
// surface) used everywhere else, instead of a custom look-alike. Fetches the
// dashboard by id; clicking outside or pressing Escape closes it.

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { GenericQCDashboard } from "./GenericQCDashboard";
import { QCAiReviewSection } from "./QCAiReviewSection";
import { QualityBadge } from "./QualityBadge";
import { PlotModal } from "@/components/shared/PlotModal";
import { useFileContentUrl } from "@/hooks/useContentUrl";
import type { QCDashboardResponse } from "@/lib/types";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";

function PlotImage({
  fileId,
  title,
  onExpand,
}: {
  fileId: number;
  title: string;
  onExpand: (url: string) => void;
}) {
  const url = useFileContentUrl(fileId);
  const [error, setError] = useState(false);

  return (
    <div className="relative bg-gray-100 rounded min-h-[12rem] flex items-center justify-center group">
      {error ? (
        <span className="text-gray-400 text-sm">Failed to load plot</span>
      ) : url ? (
        <>
          <img src={url} alt={title} className="w-full rounded" onError={() => setError(true)} />
          <button
            onClick={() => onExpand(url)}
            className="absolute top-2 right-2 p-1.5 bg-white/80 rounded shadow opacity-0 group-hover:opacity-100 transition-opacity hover:bg-white"
            title="Expand plot"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-4 w-4 text-gray-600"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5v-4m0 4h-4m4 0l-5-5"
              />
            </svg>
          </button>
        </>
      ) : (
        <span className="text-gray-400 text-sm">Loading plot...</span>
      )}
    </div>
  );
}

export function QCReportModal({
  dashboardId,
  onClose,
}: {
  dashboardId: number;
  onClose: () => void;
}) {
  const [dashboard, setDashboard] = useState<QCDashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<{ url: string; title: string } | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .get<QCDashboardResponse>(`/api/qc-dashboards/${dashboardId}`)
      .then((d) => {
        if (alive) setDashboard(d);
      })
      .catch((e) => {
        if (alive) setError((e as Error).message);
      });
    return () => {
      alive = false;
    };
  }, [dashboardId]);

  useDismissOnEscape(true, onClose);

  const pipelineLabel = dashboard?.pipeline_name
    ? `${dashboard.pipeline_name}${dashboard.pipeline_version ? ` v${dashboard.pipeline_version}` : ""}`
    : null;
  const contextParts = dashboard
    ? [dashboard.project_name, dashboard.experiment_name, pipelineLabel].filter(Boolean)
    : [];

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      data-testid="qc-report-modal"
    >
      <div className="bg-white rounded-lg shadow-xl w-full max-w-4xl max-h-[85vh] overflow-y-auto p-6">
        {error ? (
          <div className="text-red-600 text-sm">{error}</div>
        ) : !dashboard ? (
          <div className="text-gray-500">Loading…</div>
        ) : (
          <>
            <div className="flex items-start justify-between mb-6">
              <div>
                <h2 className="text-lg font-bold">QC Dashboard</h2>
                <p className="text-sm text-gray-600 mt-0.5">
                  {contextParts.length > 0 && <span>{contextParts.join(" / ")} / </span>}
                  <Link
                    href={`/pipelines/runs/${dashboard.pipeline_run_id}`}
                    className="text-bioaf-600 hover:underline"
                  >
                    Run #{dashboard.pipeline_run_id}
                  </Link>
                </p>
              </div>
              <div className="flex items-center gap-2">
                <QualityBadge rating={dashboard.metrics.quality_rating} />
                <button onClick={onClose} className="text-gray-500 hover:text-gray-700">
                  ✕
                </button>
              </div>
            </div>

            <QCAiReviewSection pipelineRunId={dashboard.pipeline_run_id} />

            <GenericQCDashboard dashboard={dashboard} />

            {dashboard.plots.length > 0 && (
              <>
                <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mt-6 mb-3">
                  Plots
                </h3>
                <div className="grid grid-cols-2 gap-4">
                  {dashboard.plots.map((plot, i) => (
                    <div key={i} className="border rounded-lg p-3">
                      <p className="text-sm font-medium mb-2">{plot.title}</p>
                      <PlotImage
                        fileId={plot.file_id}
                        title={plot.title}
                        onExpand={(url) => setExpanded({ url, title: plot.title })}
                      />
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>
      {expanded && (
        <PlotModal url={expanded.url} title={expanded.title} onClose={() => setExpanded(null)} />
      )}
    </div>
  );
}
