"use client";

import { useState, useEffect, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { PlotModal } from "@/components/shared/PlotModal";
import { ExportPdfButton } from "@/components/shared/ExportPdfButton";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { GenericQCDashboard } from "@/components/qc/GenericQCDashboard";
import { QCAiReviewSection } from "@/components/qc/QCAiReviewSection";
import { QualityBadge } from "@/components/qc/QualityBadge";
import { QCDashboardListItem } from "@/components/qc/QCDashboardListItem";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { useFileContentUrl } from "@/hooks/useContentUrl";
import type { QCDashboardSummary, QCDashboardResponse } from "@/lib/types";
import { ErrorState } from "@/components/shared/ErrorState";

function PlotImage({ fileId, title, onExpand }: { fileId: number; title: string; onExpand: (url: string) => void }) {
  const url = useFileContentUrl(fileId);
  const [error, setError] = useState(false);

  return (
    <div className="relative bg-gray-100 rounded min-h-[12rem] flex items-center justify-center group">
      {error ? (
        <span className="text-gray-500 text-sm">Failed to load plot</span>
      ) : url ? (
        <>
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

function DashboardDetail({ dashboard, onBack, onRegenerate, regenerating, onExpandPlot }: {
  dashboard: QCDashboardResponse;
  onBack: () => void;
  onRegenerate: (runId: number) => void;
  regenerating: boolean;
  onExpandPlot: (url: string, title: string) => void;
}) {
  const rating = dashboard.metrics.quality_rating;
  const pipelineLabel = dashboard.pipeline_name
    ? `${dashboard.pipeline_name}${dashboard.pipeline_version ? ` v${dashboard.pipeline_version}` : ""}`
    : null;
  const contextParts = [dashboard.project_name, dashboard.experiment_name, pipelineLabel].filter(
    Boolean,
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="text-bioaf-600 text-sm hover:underline">
          Back to list
        </button>
        <ExportPdfButton
          targetId="qc-dashboard-content"
          filename={`qc-dashboard-run-${dashboard.pipeline_run_id}.pdf`}
        />
      </div>

      <div id="qc-dashboard-content" className="bg-white rounded-lg shadow p-6">
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
            <button
              onClick={() => onRegenerate(dashboard.pipeline_run_id)}
              disabled={regenerating}
              className="px-3 py-1 text-xs font-medium text-gray-600 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-50 print:hidden"
              data-html2canvas-ignore="true"
            >
              {regenerating ? "Regenerating..." : "Regenerate"}
            </button>
            <QualityBadge rating={rating} />
          </div>
        </div>

        <div data-html2canvas-ignore="true">
          <QCAiReviewSection pipelineRunId={dashboard.pipeline_run_id} />
        </div>

        <GenericQCDashboard dashboard={dashboard} />

        {dashboard.plots.length > 0 && (
          <>
            <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mt-6 mb-3">Plots</h3>
            <div className="grid grid-cols-2 gap-4">
              {dashboard.plots.map((plot, i) => (
                <div key={i} className="border rounded-lg p-3">
                  <p className="text-sm font-medium mb-2">{plot.title}</p>
                  <PlotImage
                    fileId={plot.file_id}
                    title={plot.title}
                    onExpand={(url) => onExpandPlot(url, plot.title)}
                  />
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function QCDashboardsPage() {
  return (
    <Suspense fallback={null}>
      <QCDashboardsPageInner />
    </Suspense>
  );
}

function QCDashboardsPageInner() {
  const [dashboards, setDashboards] = useState<QCDashboardSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Bumped by Retry: the load lives in an effect with no named loader.
  const [reloadKey, setReloadKey] = useState(0);
  const [selected, setSelected] = useState<QCDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  // Three requests were swallowed whole here: opening a dashboard, the deep
  // link the "results ready" notification lands on, and Regenerate (a POST).
  // Proven on the demo: with the per-dashboard GET failing, clicking a row left
  // the page text BYTE-IDENTICAL. A dead control is worse than an error.
  const [actionError, setActionError] = useState<string | null>(null);
  const [expandedPlot, setExpandedPlot] = useState<{ url: string; title: string } | null>(null);
  const searchParams = useSearchParams();

  useEffect(() => {
    (async () => {
      try {
        const data = await api.get<QCDashboardSummary[]>("/api/qc-dashboards");
        setDashboards(data);
        // A Retry bumps `reloadKey` and re-runs this effect. Without clearing here, a
        // successful retry still showed the failure sentence, so the only recovery was
        // a full page reload.
        setLoadError(null);
      } catch (e) {
        logError("loading QC dashboards", e);
        setLoadError(loadFailureMessage("QC dashboards"));
      } finally {
        setLoading(false);
      }
    })();
  }, [reloadKey]);

  // Deep link: ?run=<id> opens that run's dashboard directly. This is where the
  // "results ready" notification lands. Falls back to the list if there is none.
  useEffect(() => {
    const run = searchParams?.get("run");
    if (!run) return;
    (async () => {
      try {
        const data = await api.get<QCDashboardResponse>(`/api/qc-dashboards/by-run/${run}`);
        setSelected(data);
        setActionError(null);
      } catch (e) {
        logError(`opening the QC dashboard for run ${run}`, e);
        setActionError(
          `The QC dashboard for run ${run} could not be opened, so the full list is shown instead. The technical detail is in the application logs.`,
        );
      }
    })();
  }, [searchParams]);

  const viewDashboard = async (id: number) => {
    try {
      const data = await api.get<QCDashboardResponse>(`/api/qc-dashboards/${id}`);
      setSelected(data);
      setActionError(null);
    } catch (e) {
      logError(`opening QC dashboard ${id}`, e);
      setActionError(loadFailureMessage("That QC dashboard"));
    }
  };

  const regenerateQc = async (runId: number) => {
    setRegenerating(true);
    setActionError(null);
    try {
      const data = await api.post<QCDashboardResponse>(`/api/qc-dashboards/regenerate/${runId}`, {});
      setSelected(data);
      const updated = await api.get<QCDashboardSummary[]>("/api/qc-dashboards");
      setDashboards(updated);
    } catch (e) {
      logError(`regenerating the QC dashboard for run ${runId}`, e);
      setActionError(
        "The QC dashboard could not be regenerated, so it is unchanged. The technical detail is in the application logs.",
      );
    } finally {
      setRegenerating(false);
    }
  };

  const handleExpandPlot = useCallback((url: string, title: string) => {
    setExpandedPlot({ url, title });
  }, []);

  return (
    <>
      <main className="flex-1 overflow-y-auto p-6">
        <h1 className="text-2xl font-bold mb-1">QC Dashboards</h1>
        <p data-testid="page-description" className="text-sm text-gray-500 mb-6">
          Quality-control summaries generated per pipeline run, with metrics, plots and an exportable report.
        </p>

        {actionError && (
          <p
            data-testid="qc-action-failed"
            role="status"
            className="mb-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3"
          >
            {actionError}
          </p>
        )}

        {selected ? (
          <DashboardDetail
            dashboard={selected}
            onBack={() => setSelected(null)}
            onRegenerate={regenerateQc}
            regenerating={regenerating}
            onExpandPlot={handleExpandPlot}
          />
        ) : loading ? (
          <ContentLoading variant="cards" />
        ) : loadError ? (
          <ErrorState message={loadError} onRetry={() => setReloadKey((k) => k + 1)} />
        ) : dashboards.length === 0 ? (
          <p className="text-gray-500 text-sm">
            No QC dashboards yet. They are generated automatically when pipeline runs complete.
          </p>
        ) : (
          <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
            {dashboards.map((d) => (
              <QCDashboardListItem key={d.id} dashboard={d} onClick={() => viewDashboard(d.id)} />
            ))}
          </div>
        )}
      </main>

      {expandedPlot && (
        <PlotModal
          url={expandedPlot.url}
          title={expandedPlot.title}
          onClose={() => setExpandedPlot(null)}
        />
      )}
    </>
  );
}
