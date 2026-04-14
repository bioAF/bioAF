"use client";

import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { PlotModal } from "@/components/shared/PlotModal";
import { api } from "@/lib/api";
import { useFileContentUrl } from "@/hooks/useContentUrl";
import { ContentLoading } from "@/components/shared/ContentLoading";
import type {
  QCDashboardSummary,
  QCDashboardResponse,
  CellxgenePublicationResponse,
  PlotArchiveResponse,
  PlotArchiveListResponse,
} from "@/lib/types";

type Tab = "qc" | "cellxgene" | "plots";

export default function ResultsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("qc");

  const tabs: { key: Tab; label: string }[] = [
    { key: "qc", label: "QC Dashboards" },
    { key: "cellxgene", label: "cellxgene" },
    { key: "plots", label: "Plot Archive" },
  ];

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-4">Results & Visualization</h1>

          <div className="border-b border-gray-200 mb-6">
            <nav className="flex space-x-8">
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`pb-3 px-1 text-sm font-medium border-b-2 ${
                    activeTab === tab.key
                      ? "border-blue-500 text-blue-600"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>

          {activeTab === "qc" && <QCDashboardsTab />}
          {activeTab === "cellxgene" && <CellxgeneTab />}
          {activeTab === "plots" && <PlotArchiveTab />}
        </main>
      </div>
    </div>
  );
}

/* ─── QC Dashboards Tab ─── */

function QCDashboardsTab() {
  const [dashboards, setDashboards] = useState<QCDashboardSummary[]>([]);
  const [selected, setSelected] = useState<QCDashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);

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

  const qualityColor = (rating: string) => {
    switch (rating) {
      case "excellent":
        return "bg-green-100 text-green-700";
      case "good":
        return "bg-blue-100 text-blue-700";
      case "acceptable":
        return "bg-yellow-100 text-yellow-700";
      default:
        return "bg-red-100 text-red-700";
    }
  };

  if (selected) {
    return (
      <div className="space-y-6">
        <button
          onClick={() => setSelected(null)}
          className="text-blue-600 text-sm hover:underline"
        >
          Back to list
        </button>

        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold">
              QC Dashboard - Run #{selected.pipeline_run_id}
            </h2>
            <span
              className={`px-3 py-1 rounded-full text-sm font-medium ${qualityColor(
                selected.metrics.quality_rating
              )}`}
            >
              {selected.metrics.quality_rating}
            </span>
          </div>

          {selected.summary_text && (
            <p className="text-sm text-gray-600 mb-6">{selected.summary_text}</p>
          )}

          {/* Metrics grid */}
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
          </div>

          {/* Plots */}
          {selected.plots.length > 0 && (
            <div>
              <h3 className="font-medium mb-3">Plots</h3>
              <div className="grid grid-cols-2 gap-4">
                {selected.plots.map((plot, i) => (
                  <div key={i} className="border rounded-lg p-3">
                    <p className="text-sm font-medium mb-2">{plot.title}</p>
                    <div className="bg-gray-100 rounded h-48 flex items-center justify-center text-gray-400 text-sm">
                      Plot: {plot.plot_type}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (loading) return <ContentLoading />;

  return (
    <div className="space-y-4">
      {dashboards.length === 0 ? (
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
                <p className="font-medium text-sm">
                  Run #{d.pipeline_run_id}
                </p>
                <p className="text-xs text-gray-400">
                  Generated {d.generated_at ? new Date(d.generated_at).toLocaleDateString() : "N/A"}
                  {d.cell_count != null && ` | ${d.cell_count.toLocaleString()} cells`}
                </p>
              </div>
              <span
                className={`px-2 py-0.5 rounded-full text-xs font-medium ${qualityColor(
                  d.quality_rating
                )}`}
              >
                {d.quality_rating}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  );
}

/* ─── cellxgene Tab ─── */

function CellxgeneTab() {
  const [publications, setPublications] = useState<CellxgenePublicationResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [showPublishForm, setShowPublishForm] = useState(false);
  const [publishForm, setPublishForm] = useState({ file_id: "", experiment_id: "", dataset_name: "" });

  const fetchPublications = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get<CellxgenePublicationResponse[]>("/api/cellxgene");
      setPublications(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPublications();
  }, [fetchPublications]);

  const handlePublish = async () => {
    try {
      await api.post("/api/cellxgene/publish", {
        file_id: parseInt(publishForm.file_id),
        experiment_id: publishForm.experiment_id ? parseInt(publishForm.experiment_id) : null,
        dataset_name: publishForm.dataset_name,
      });
      setShowPublishForm(false);
      setPublishForm({ file_id: "", experiment_id: "", dataset_name: "" });
      fetchPublications();
    } catch {
      // ignore
    }
  };

  const handleUnpublish = async (id: number) => {
    if (!confirm("Unpublish this dataset?")) return;
    try {
      await api.delete(`/api/cellxgene/${id}`);
      fetchPublications();
    } catch {
      // ignore
    }
  };

  if (loading) return <ContentLoading />;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <p className="text-sm text-gray-500">
          Publish h5ad datasets for interactive exploration with cellxgene.
        </p>
        <button
          onClick={() => setShowPublishForm(!showPublishForm)}
          className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700"
        >
          Publish Dataset
        </button>
      </div>

      {showPublishForm && (
        <div className="bg-white rounded-lg shadow p-4 space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              File ID (h5ad)
            </label>
            <input
              type="text"
              value={publishForm.file_id}
              onChange={(e) =>
                setPublishForm((f) => ({ ...f, file_id: e.target.value }))
              }
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Dataset Name
            </label>
            <input
              type="text"
              value={publishForm.dataset_name}
              onChange={(e) =>
                setPublishForm((f) => ({ ...f, dataset_name: e.target.value }))
              }
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Experiment ID (optional)
            </label>
            <input
              type="text"
              value={publishForm.experiment_id}
              onChange={(e) =>
                setPublishForm((f) => ({ ...f, experiment_id: e.target.value }))
              }
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
            />
          </div>
          <button
            onClick={handlePublish}
            disabled={!publishForm.file_id || !publishForm.dataset_name}
            className="px-4 py-2 bg-green-600 text-white rounded-md text-sm hover:bg-green-700 disabled:opacity-50"
          >
            Publish
          </button>
        </div>
      )}

      {publications.length === 0 ? (
        <p className="text-gray-400 text-sm">No published datasets.</p>
      ) : (
        <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
          {publications.map((pub) => (
            <div
              key={pub.id}
              className="p-4 flex items-center justify-between hover:bg-gray-50"
            >
              <div>
                <p className="font-medium text-sm">{pub.dataset_name}</p>
                <p className="text-xs text-gray-400">
                  Status: {pub.status}
                  {pub.published_at &&
                    ` | Published ${new Date(pub.published_at).toLocaleDateString()}`}
                </p>
              </div>
              <div className="flex gap-3 items-center">
                {pub.stable_url && pub.status === "running" && (
                  <a
                    href={pub.stable_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 text-sm hover:underline"
                  >
                    Open
                  </a>
                )}
                <button
                  onClick={() => handleUnpublish(pub.id)}
                  className="text-red-500 text-sm hover:underline"
                >
                  Unpublish
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Plot Archive Tab ─── */

function PlotThumbnail({
  fileId,
  title,
  onClick,
}: {
  fileId: number;
  title: string;
  onClick: (url: string) => void;
}) {
  const [error, setError] = useState(false);
  const url = useFileContentUrl(fileId);

  if (error) return <span className="text-gray-400 text-xs">Failed to load</span>;
  if (!url) return <span className="text-gray-400 text-xs">Loading...</span>;
  return (
    <img
      src={url}
      alt={title}
      className="w-full h-full object-cover cursor-pointer"
      onClick={() => onClick(url)}
      onError={() => setError(true)}
    />
  );
}

function PlotArchiveTab() {
  const [plots, setPlots] = useState<PlotArchiveResponse[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [expandedUrl, setExpandedUrl] = useState<string | null>(null);
  const [expandedTitle, setExpandedTitle] = useState("");
  const pageSize = 24;

  const fetchPlots = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
      });
      if (query) params.set("query", query);
      const data = await api.get<PlotArchiveListResponse>(
        `/api/plots?${params}`
      );
      setPlots(data.plots);
      setTotal(data.total);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [page, query]);

  useEffect(() => {
    fetchPlots();
  }, [fetchPlots]);

  return (
    <div className="space-y-4">
      <input
        type="text"
        placeholder="Search plots..."
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setPage(1);
        }}
        className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
      />

      {loading ? (
        <ContentLoading />
      ) : plots.length === 0 ? (
        <p className="text-gray-400 text-sm">No plots found.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
            {plots.map((plot) => (
              <div
                key={plot.id}
                className="bg-white rounded-lg shadow overflow-hidden hover:shadow-md transition-shadow"
              >
                <div className="aspect-square bg-gray-100 flex items-center justify-center">
                  {plot.file ? (
                    <PlotThumbnail
                      fileId={plot.file.id}
                      title={plot.title ?? "Plot"}
                      onClick={(url) => {
                        setExpandedUrl(url);
                        setExpandedTitle(plot.title ?? "Plot");
                      }}
                    />
                  ) : (
                    <span className="text-gray-400 text-xs">No preview</span>
                  )}
                </div>
                <div className="p-2">
                  <p className="text-xs font-medium truncate">{plot.title}</p>
                  {plot.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {plot.tags.slice(0, 3).map((tag) => (
                        <span
                          key={tag}
                          className="px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px]"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="flex justify-between items-center text-sm text-gray-500">
            <span>
              {total} plot{total !== 1 ? "s" : ""}
            </span>
            <div className="space-x-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                Previous
              </button>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={page * pageSize >= total}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}

      {expandedUrl && (
        <PlotModal
          url={expandedUrl}
          title={expandedTitle}
          onClose={() => setExpandedUrl(null)}
        />
      )}
    </div>
  );
}
