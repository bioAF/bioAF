"use client";

import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { PlotModal } from "@/components/shared/PlotModal";
import { ExportPdfButton } from "@/components/shared/ExportPdfButton";
import { api } from "@/lib/api";
import type { QCDashboardSummary, QCDashboardResponse } from "@/lib/types";

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  );
}

function PlotImage({ fileId, title, onExpand }: { fileId: number; title: string; onExpand: (url: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<{ download_url: string }>(`/api/files/${fileId}/download`);
        if (!cancelled) setUrl(data.download_url);
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => { cancelled = true; };
  }, [fileId]);

  return (
    <div className="relative bg-gray-100 rounded min-h-[12rem] flex items-center justify-center group">
      {error ? (
        <span className="text-gray-400 text-sm">Failed to load plot</span>
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
        <span className="text-gray-400 text-sm">Loading plot...</span>
      )}
    </div>
  );
}

export default function QCDashboardsPage() {
  const [dashboards, setDashboards] = useState<QCDashboardSummary[]>([]);
  const [selected, setSelected] = useState<QCDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  const [expandedPlot, setExpandedPlot] = useState<{ url: string; title: string } | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.get<QCDashboardSummary[]>("/api/qc-dashboards");
        setDashboards(data);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const viewDashboard = async (id: number) => {
    try {
      const data = await api.get<QCDashboardResponse>(`/api/qc-dashboards/${id}`);
      setSelected(data);
    } catch {
      // ignore
    }
  };

  const regenerateQc = async (runId: number) => {
    setRegenerating(true);
    try {
      const data = await api.post<QCDashboardResponse>(`/api/qc-dashboards/regenerate/${runId}`, {});
      setSelected(data);
      const updated = await api.get<QCDashboardSummary[]>("/api/qc-dashboards");
      setDashboards(updated);
    } catch {
      // ignore
    } finally {
      setRegenerating(false);
    }
  };

  const handleExpandPlot = useCallback((url: string, title: string) => {
    setExpandedPlot({ url, title });
  }, []);

  const qualityColor = (rating: string) => {
    switch (rating) {
      case "excellent": return "bg-green-100 text-green-700";
      case "good": return "bg-blue-100 text-blue-700";
      case "acceptable": return "bg-yellow-100 text-yellow-700";
      case "pending_review": return "bg-gray-100 text-gray-700";
      default: return "bg-red-100 text-red-700";
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">QC Dashboards</h1>

          {selected ? (
            <div className="space-y-6">
              <div className="flex items-center justify-between">
                <button onClick={() => setSelected(null)} className="text-blue-600 text-sm hover:underline">
                  Back to list
                </button>
                <ExportPdfButton
                  targetId="qc-dashboard-content"
                  filename={`qc-dashboard-run-${selected.pipeline_run_id}.pdf`}
                />
              </div>

              <div id="qc-dashboard-content" className="bg-white rounded-lg shadow p-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-bold">QC Dashboard - Run #{selected.pipeline_run_id}</h2>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => regenerateQc(selected.pipeline_run_id)}
                      disabled={regenerating}
                      className="px-3 py-1 text-xs font-medium text-gray-600 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-50"
                    >
                      {regenerating ? "Regenerating..." : "Regenerate"}
                    </button>
                    <span className={`px-3 py-1 rounded-full text-sm font-medium ${qualityColor(selected.metrics.quality_rating)}`}>
                      {selected.metrics.quality_rating}
                    </span>
                  </div>
                </div>

                {selected.summary_text && (
                  <p className="text-sm text-gray-600 mb-6" dangerouslySetInnerHTML={{ __html: selected.summary_text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>") }} />
                )}

                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                  {selected.metrics.cell_count != null && (
                    <MetricCard label="Cell Count" value={selected.metrics.cell_count.toLocaleString()} />
                  )}
                  {selected.metrics.median_genes_per_cell != null && (
                    <MetricCard label="Median Genes/Cell" value={selected.metrics.median_genes_per_cell.toLocaleString()} />
                  )}
                  {selected.metrics.median_umi_per_cell != null && (
                    <MetricCard label="Median UMI/Cell" value={selected.metrics.median_umi_per_cell.toLocaleString()} />
                  )}
                  {selected.metrics.mito_pct_median != null && (
                    <MetricCard label="Mito % Median" value={`${selected.metrics.mito_pct_median.toFixed(1)}%`} />
                  )}
                  {selected.metrics.median_reads_per_cell != null && (
                    <MetricCard label="Median Reads/Cell" value={selected.metrics.median_reads_per_cell.toLocaleString()} />
                  )}
                  {selected.metrics.doublet_score_median != null && (
                    <MetricCard label="Doublet Score" value={selected.metrics.doublet_score_median.toFixed(3)} />
                  )}
                  {selected.metrics.saturation != null && (
                    <MetricCard label="Saturation" value={`${(selected.metrics.saturation * 100).toFixed(1)}%`} />
                  )}
                  {selected.metrics.total_sequences != null && (
                    <MetricCard label="Total Sequences" value={selected.metrics.total_sequences.toLocaleString()} />
                  )}
                  {selected.metrics.total_samples != null && (
                    <MetricCard label="Samples" value={String(selected.metrics.total_samples)} />
                  )}
                  {selected.metrics.percent_duplicates != null && (
                    <MetricCard label="Duplication" value={`${selected.metrics.percent_duplicates.toFixed(1)}%`} />
                  )}
                  {selected.metrics.percent_gc != null && (
                    <MetricCard label="GC Content" value={`${selected.metrics.percent_gc.toFixed(0)}%`} />
                  )}
                  {selected.metrics.avg_sequence_length != null && (
                    <MetricCard label="Avg Read Length" value={`${selected.metrics.avg_sequence_length.toFixed(0)} bp`} />
                  )}
                </div>

                {selected.plots.length > 0 && (
                  <div>
                    <h3 className="font-medium mb-3">Plots</h3>
                    <div className="grid grid-cols-2 gap-4">
                      {selected.plots.map((plot, i) => (
                        <div key={i} className="border rounded-lg p-3">
                          <p className="text-sm font-medium mb-2">{plot.title}</p>
                          <PlotImage
                            fileId={plot.file_id}
                            title={plot.title}
                            onExpand={(url) => handleExpandPlot(url, plot.title)}
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : loading ? (
            <p className="text-gray-400 text-sm">Loading...</p>
          ) : dashboards.length === 0 ? (
            <p className="text-gray-400 text-sm">
              No QC dashboards yet. They are generated automatically when pipeline runs complete.
            </p>
          ) : (
            <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
              {dashboards.map((d) => (
                <div
                  key={d.id}
                  onClick={() => viewDashboard(d.id)}
                  className="p-4 flex items-center justify-between hover:bg-gray-50 cursor-pointer"
                >
                  <div>
                    <p className="font-medium text-sm">Run #{d.pipeline_run_id}</p>
                    <p className="text-xs text-gray-400">
                      Generated {d.generated_at ? new Date(d.generated_at).toLocaleDateString() : "N/A"}
                      {d.cell_count != null && ` | ${d.cell_count.toLocaleString()} cells`}
                    </p>
                  </div>
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${qualityColor(d.quality_rating)}`}>
                    {d.quality_rating}
                  </span>
                </div>
              ))}
            </div>
          )}
        </main>
      </div>

      {expandedPlot && (
        <PlotModal
          url={expandedPlot.url}
          title={expandedPlot.title}
          onClose={() => setExpandedPlot(null)}
        />
      )}
    </div>
  );
}
