"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { GenericQCDashboard } from "@/components/qc/GenericQCDashboard";
import { QCAiReviewSection } from "@/components/qc/QCAiReviewSection";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { PlotModal } from "@/components/shared/PlotModal";
import { api, fileContentUrl, plotThumbnailContentUrl } from "@/lib/api";
import { useFileContentUrl, usePlotThumbnailContentUrl } from "@/hooks/useContentUrl";
import type {
  PlotArchiveListResponse,
  PlotArchiveResponse,
  QCDashboardResponse,
} from "@/lib/types";

interface Props {
  pipelineRunId: number;
}

function QCDashboardPlot({
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
        <span className="text-gray-500 text-sm">Failed to load plot</span>
      ) : url ? (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={title}
            className="w-full rounded"
            onError={() => setError(true)}
          />
          <button
            onClick={() => onExpand(url)}
            className="absolute top-2 right-2 p-1.5 bg-white/80 rounded shadow opacity-0 group-hover:opacity-100 transition-opacity hover:bg-white"
            title="Expand plot"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5v-4m0 4h-4m4 0l-5-5" />
            </svg>
          </button>
        </>
      ) : (
        <span className="text-gray-500 text-sm">Loading plot...</span>
      )}
    </div>
  );
}

function PlotThumbnail({
  plot,
  onClick,
}: {
  plot: PlotArchiveResponse;
  onClick: () => void;
}) {
  const [error, setError] = useState(false);
  const fileType = plot.file?.file_type?.toLowerCase() ?? "";
  const isPdf = fileType === "pdf";
  const hasThumbnail = !!plot.thumbnail_url;

  const thumbnailUrl = usePlotThumbnailContentUrl(isPdf && hasThumbnail ? plot.id : null);
  const fileUrl = useFileContentUrl(!isPdf || !hasThumbnail ? (plot.file?.id ?? null) : null);
  const imgUrl = (isPdf && hasThumbnail ? thumbnailUrl : fileUrl) ?? "";

  if (isPdf && !hasThumbnail) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="flex flex-col items-center gap-2 py-6 cursor-pointer hover:opacity-80"
      >
        <div className="w-16 h-16 bg-gray-200 rounded-lg flex items-center justify-center text-gray-500 text-xs font-bold uppercase">
          PDF
        </div>
        <span className="text-xs text-gray-500">No preview</span>
      </button>
    );
  }

  if (error) {
    return (
      <button
        type="button"
        onClick={onClick}
        className="flex flex-col items-center gap-2 py-6 cursor-pointer hover:opacity-80"
      >
        <div className="w-16 h-16 bg-gray-200 rounded-lg flex items-center justify-center text-gray-500 text-xs font-bold uppercase">
          {fileType || "?"}
        </div>
        <span className="text-xs text-gray-500">No preview</span>
      </button>
    );
  }

  if (!imgUrl) {
    return <div className="w-full h-full bg-gray-100 animate-pulse" />;
  }

  return (
    // A real button rather than role="button" on the image: the thumbnail
    // opens the full plot, and a clickable image is mouse-only. Wrapping keeps
    // native focus and Enter/Space handling, and the alt text becomes the
    // button's accessible name instead of being replaced by one.
    <button
      type="button"
      onClick={onClick}
      className="block w-full h-full cursor-pointer"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={imgUrl}
        alt={plot.title ?? "Plot"}
        className="w-full h-full object-cover"
        onError={() => setError(true)}
      />
    </button>
  );
}

export function PipelineRunResultsTab({ pipelineRunId }: Props) {
  const [dashboard, setDashboard] = useState<QCDashboardResponse | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(true);
  const [dashboardMissing, setDashboardMissing] = useState(false);

  const [plots, setPlots] = useState<PlotArchiveResponse[]>([]);
  const [plotsLoading, setPlotsLoading] = useState(true);

  const [expandedUrl, setExpandedUrl] = useState<string | null>(null);
  const [expandedTitle, setExpandedTitle] = useState("");
  const [expandedPlot, setExpandedPlot] = useState<PlotArchiveResponse | null>(null);
  const [expandedQCUrl, setExpandedQCUrl] = useState<{ url: string; title: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setDashboardLoading(true);
      setDashboardMissing(false);
      try {
        const data = await api.get<QCDashboardResponse>(
          `/api/qc-dashboards/by-run/${pipelineRunId}`,
        );
        if (!cancelled) setDashboard(data);
      } catch {
        if (!cancelled) {
          setDashboard(null);
          setDashboardMissing(true);
        }
      } finally {
        if (!cancelled) setDashboardLoading(false);
      }
    })();
    (async () => {
      setPlotsLoading(true);
      try {
        const params = new URLSearchParams({
          pipeline_run_id: String(pipelineRunId),
          page: "1",
          page_size: "24",
        });
        const data = await api.get<PlotArchiveListResponse>(`/api/plots?${params}`);
        if (!cancelled) setPlots(data.plots);
      } catch {
        if (!cancelled) setPlots([]);
      } finally {
        if (!cancelled) setPlotsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pipelineRunId]);

  const handleExpand = useCallback(async (plot: PlotArchiveResponse) => {
    const isPdf = plot.file?.file_type?.toLowerCase() === "pdf";
    const url = isPdf && plot.thumbnail_url
      ? await plotThumbnailContentUrl(plot.id)
      : plot.file
        ? await fileContentUrl(plot.file.id)
        : "";
    setExpandedUrl(url);
    setExpandedTitle(plot.title ?? "Plot");
    setExpandedPlot(plot);
  }, []);

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">QC Dashboard</h2>
          <Link
            href={`/results/qc-dashboards?pipeline_run_id=${pipelineRunId}`}
            className="text-sm text-bioaf-600 hover:underline"
          >
            Open in QC Dashboards
          </Link>
        </div>
        <QCAiReviewSection pipelineRunId={pipelineRunId} />
        {dashboardLoading ? (
          <div className="flex items-center gap-2 text-gray-500">
            <LoadingSpinner size="sm" />
            <span>Loading QC dashboard...</span>
          </div>
        ) : dashboard ? (
          <>
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
                      <QCDashboardPlot
                        fileId={plot.file_id}
                        title={plot.title}
                        onExpand={(url) =>
                          setExpandedQCUrl({ url, title: plot.title })
                        }
                      />
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        ) : dashboardMissing ? (
          <p className="text-sm text-gray-500">
            No QC dashboard yet for this run. Dashboards are generated automatically when the run completes.
          </p>
        ) : null}
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Plot Archive</h2>
          <Link
            href={`/results/plot-archive?pipeline_run_id=${pipelineRunId}`}
            className="text-sm text-bioaf-600 hover:underline"
          >
            Open in Plot Archive
          </Link>
        </div>
        {plotsLoading ? (
          <div className="flex items-center gap-2 text-gray-500">
            <LoadingSpinner size="sm" />
            <span>Loading plots...</span>
          </div>
        ) : plots.length === 0 ? (
          <p className="text-sm text-gray-500">No plots indexed for this run yet.</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
            {plots.map((plot) => (
              <div
                key={plot.id}
                className="bg-white border rounded-lg overflow-hidden hover:shadow-md transition-shadow"
              >
                <div className="aspect-square bg-gray-100 flex items-center justify-center relative">
                  {plot.file ? (
                    <PlotThumbnail plot={plot} onClick={() => handleExpand(plot)} />
                  ) : (
                    <span className="text-gray-500 text-xs">No preview</span>
                  )}
                  {plot.file && (
                    <span className="absolute top-1.5 right-1.5 px-1.5 py-0.5 bg-black/70 text-white text-[10px] font-semibold uppercase rounded">
                      {plot.file.file_type}
                    </span>
                  )}
                </div>
                <div className="p-2">
                  <p
                    className="text-[11px] leading-tight font-medium line-clamp-2"
                    title={plot.title ?? undefined}
                  >
                    {plot.title}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {expandedUrl && expandedPlot && (
        <PlotModal
          url={expandedUrl}
          title={expandedTitle}
          metadata={{
            experimentName: expandedPlot.experiment_name,
            projectName: expandedPlot.project_name,
            pipelineRunId: expandedPlot.pipeline_run_id,
            pipelineRunName: expandedPlot.pipeline_run_name,
            notebookSessionId: expandedPlot.notebook_session_id,
            notebookSessionType: expandedPlot.notebook_session_type,
            sourceType: expandedPlot.source_type,
            tags: expandedPlot.tags,
            indexedAt: expandedPlot.indexed_at,
            file: expandedPlot.file,
          }}
          onClose={() => {
            setExpandedUrl(null);
            setExpandedPlot(null);
          }}
        />
      )}

      {expandedQCUrl && (
        <PlotModal
          url={expandedQCUrl.url}
          title={expandedQCUrl.title}
          onClose={() => setExpandedQCUrl(null)}
        />
      )}
    </div>
  );
}
