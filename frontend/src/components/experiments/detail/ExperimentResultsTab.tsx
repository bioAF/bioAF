"use client";

import { useState } from "react";
import { api, fileContentUrl, plotThumbnailContentUrl } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { PlotModal } from "@/components/shared/PlotModal";
import { QCReportModal } from "@/components/qc/QCReportModal";
import { QCDashboardListItem } from "@/components/qc/QCDashboardListItem";
import { PlotThumbnail, StorageDeletedPlaceholder } from "@/components/plots/PlotThumbnail";
import type {
  CellxgenePublicationResponse,
  PlotArchiveListResponse,
  PlotArchiveResponse,
  QCDashboardSummary,
} from "@/lib/types";

function useSection<T>(experimentId: number, section: string, path: string, what: string) {
  return useResource<T>(`experiment:${experimentId}:results:${section}`, (signal) => api.get<T>(path, { signal }), {
    onError: (e) => logError(`loading ${what} for experiment ${experimentId}`, e),
  });
}

export function ExperimentResultsTab({ experimentId }: { experimentId: number }) {
  // Three independent reads: one failure reports only its own section, and a section that loaded
  // is still shown. Their single `Promise.all` once turned one plots timeout into three false
  // "nothing here" claims about the experiment.
  const qc = useSection<QCDashboardSummary[]>(experimentId, "qc", `/api/qc-dashboards?experiment_id=${experimentId}`, "QC dashboards");
  const pubs = useSection<CellxgenePublicationResponse[]>(experimentId, "pubs", `/api/cellxgene?experiment_id=${experimentId}`, "cellxgene datasets");
  const plotData = useSection<PlotArchiveListResponse>(experimentId, "plots", `/api/plots?experiment_id=${experimentId}&page_size=12`, "plots");
  const [modalDashboardId, setModalDashboardId] = useState<number | null>(null);
  const [expandedUrl, setExpandedUrl] = useState<string | null>(null);
  const [expandedTitle, setExpandedTitle] = useState("");
  const [expandedPlot, setExpandedPlot] = useState<PlotArchiveResponse | null>(null);

  const loading = qc.loading || pubs.loading || plotData.loading;
  const qcDashboards = qc.data ?? [];
  const cellxgenePubs = pubs.data ?? [];
  const plots = plotData.data?.plots ?? [];
  const failedSections = {
    qc: qc.data === undefined && qc.error != null,
    pubs: pubs.data === undefined && pubs.error != null,
    plots: plotData.data === undefined && plotData.error != null,
  };

  const handleExpand = async (plot: PlotArchiveResponse) => {
    const isPdf = plot.file?.file_type?.toLowerCase() === "pdf";
    const url =
      isPdf && plot.thumbnail_url
        ? await plotThumbnailContentUrl(plot.id)
        : plot.file
          ? await fileContentUrl(plot.file.id)
          : "";
    setExpandedUrl(url);
    setExpandedTitle(plot.title ?? "Plot");
    setExpandedPlot(plot);
  };

  if (loading) return <p className="text-ink-subtle text-sm">Loading results...</p>;

  return (
    <div className="space-y-8">
      {/* QC Dashboards */}
      <section>
        <h2 className="text-lg font-semibold mb-3">QC Dashboards</h2>
        {failedSections.qc ? (
          <p data-testid="qc-section-failed" role="status" className="text-ink-subtle text-sm">
            {loadFailureMessage("QC dashboards")}
          </p>
        ) : qcDashboards.length === 0 ? (
          <p className="text-ink-subtle text-sm">No QC dashboards for this experiment.</p>
        ) : (
          <div className="bg-surface rounded-lg shadow divide-y divide-hairline">
            {qcDashboards.map((d) => (
              <QCDashboardListItem
                key={d.id}
                dashboard={d}
                onClick={() => setModalDashboardId(d.id)}
              />
            ))}
          </div>
        )}
      </section>

      {/* cellxgene Publications */}
      <section>
        <h2 className="text-lg font-semibold mb-3">cellxgene Datasets</h2>
        {failedSections.pubs ? (
          <p data-testid="pubs-section-failed" role="status" className="text-ink-subtle text-sm">
            {loadFailureMessage("Published datasets")}
          </p>
        ) : cellxgenePubs.length === 0 ? (
          <p className="text-ink-subtle text-sm">No published datasets for this experiment.</p>
        ) : (
          <div className="bg-surface rounded-lg shadow divide-y divide-hairline">
            {cellxgenePubs.map((pub) => (
              <div key={pub.id} className="p-4 flex items-center justify-between">
                <div>
                  <p className="font-medium text-sm">{pub.dataset_name}</p>
                  <p className="text-xs text-ink-subtle">Status: {pub.status}</p>
                </div>
                {pub.stable_url && pub.status === "running" && (
                  <a href={pub.stable_url} target="_blank" rel="noopener noreferrer" className="text-bioaf-600 text-sm hover:underline">Open</a>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Plot Archive */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Plots</h2>
        {failedSections.plots ? (
          <p data-testid="plots-section-failed" role="status" className="text-ink-subtle text-sm">
            {loadFailureMessage("Plots")}
          </p>
        ) : plots.length === 0 ? (
          <p className="text-ink-subtle text-sm">No plots for this experiment.</p>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {plots.map((plot) => {
              const deleted = plot.file?.storage_deleted === true;
              return (
                <div
                  key={plot.id}
                  className={`bg-surface rounded-lg shadow overflow-hidden transition-shadow ${deleted ? "opacity-60" : "hover:shadow-md"}`}
                >
                  <div className="aspect-square bg-elevated flex items-center justify-center relative">
                    {deleted ? (
                      <StorageDeletedPlaceholder />
                    ) : plot.file ? (
                      <PlotThumbnail plot={plot} onClick={() => handleExpand(plot)} />
                    ) : (
                      <span className="text-ink-subtle text-xs">No preview</span>
                    )}
                    {plot.file && (
                      <span className="absolute top-1.5 right-1.5 px-1.5 py-0.5 bg-black/70 text-white text-[10px] font-semibold uppercase rounded">
                        {plot.file.file_type}
                      </span>
                    )}
                  </div>
                  <div className="p-2">
                    <p
                      className={`text-[11px] leading-tight font-medium line-clamp-2 ${deleted ? "text-ink-subtle" : ""}`}
                      title={plot.title ?? undefined}
                    >
                      {plot.title}
                    </p>
                    {plot.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {plot.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="px-1.5 py-0.5 bg-elevated text-gray-600 rounded text-[10px]"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {modalDashboardId !== null && (
        <QCReportModal
          dashboardId={modalDashboardId}
          onClose={() => setModalDashboardId(null)}
        />
      )}

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
    </div>
  );
}
