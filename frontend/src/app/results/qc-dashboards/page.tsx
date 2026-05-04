"use client";

import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { PlotModal } from "@/components/shared/PlotModal";
import { ExportPdfButton } from "@/components/shared/ExportPdfButton";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { GenericQCDashboard } from "@/components/qc/GenericQCDashboard";
import { api } from "@/lib/api";
import { useFileContentUrl } from "@/hooks/useContentUrl";
import type { QCDashboardSummary, QCDashboardResponse } from "@/lib/types";

function PlotImage({ fileId, title, onExpand }: { fileId: number; title: string; onExpand: (url: string) => void }) {
  const url = useFileContentUrl(fileId);
  const [error, setError] = useState(false);

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

function DashboardDetail({ dashboard, onBack, onRegenerate, regenerating, onExpandPlot }: {
  dashboard: QCDashboardResponse;
  onBack: () => void;
  onRegenerate: (runId: number) => void;
  regenerating: boolean;
  onExpandPlot: (url: string, title: string) => void;
}) {
  const rating = dashboard.metrics.quality_rating;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="text-blue-600 text-sm hover:underline">
          Back to list
        </button>
        <ExportPdfButton
          targetId="qc-dashboard-content"
          filename={`qc-dashboard-run-${dashboard.pipeline_run_id}.pdf`}
        />
      </div>

      <div id="qc-dashboard-content" className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-bold">QC Dashboard - Run #{dashboard.pipeline_run_id}</h2>
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

function QualityBadge({ rating }: { rating: string }) {
  const colorClass = (() => {
    switch (rating) {
      case "excellent": return "bg-green-100 text-green-700";
      case "good": return "bg-blue-100 text-blue-700";
      case "acceptable": return "bg-yellow-100 text-yellow-700";
      case "pending_review": return "bg-gray-100 text-gray-700";
      default: return "bg-red-100 text-red-700";
    }
  })();

  return (
    <span className={`px-3 py-1 rounded-full text-sm font-medium ${colorClass}`}>
      {rating}
    </span>
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

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">QC Dashboards</h1>

          {selected ? (
            <DashboardDetail
              dashboard={selected}
              onBack={() => setSelected(null)}
              onRegenerate={regenerateQc}
              regenerating={regenerating}
              onExpandPlot={handleExpandPlot}
            />
          ) : loading ? (
            <ContentLoading />
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
                  <QualityBadge rating={d.quality_rating} />
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
