"use client";

import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { PlotModal } from "@/components/shared/PlotModal";
import { api } from "@/lib/api";
import type {
  PlotArchiveResponse,
  PlotArchiveListResponse,
  ExperimentListResponse,
  PipelineRunListResponse,
} from "@/lib/types";

function PlotThumbnail({
  fileId,
  title,
  onClick,
}: {
  fileId: number;
  title: string;
  onClick: (signedUrl: string) => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.get<{ download_url: string }>(
          `/api/files/${fileId}/download`
        );
        if (!cancelled) setUrl(data.download_url);
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fileId]);

  if (error) {
    return <span className="text-gray-400 text-xs">Failed to load</span>;
  }
  if (!url) {
    return <span className="text-gray-400 text-xs">Loading...</span>;
  }
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

export default function PlotArchivePage() {
  const [plots, setPlots] = useState<PlotArchiveResponse[]>([]);
  const [query, setQuery] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [pipelineRunId, setPipelineRunId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [expandedUrl, setExpandedUrl] = useState<string | null>(null);
  const [expandedTitle, setExpandedTitle] = useState("");
  const pageSize = 24;

  const [experiments, setExperiments] = useState<
    { id: number; name: string }[]
  >([]);
  const [pipelineRuns, setPipelineRuns] = useState<
    { id: number; label: string }[]
  >([]);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.get<ExperimentListResponse>(
          "/api/experiments?page_size=200"
        );
        setExperiments(
          data.experiments.map((e) => ({
            id: e.id,
            name: e.name ?? `Experiment #${e.id}`,
          }))
        );
      } catch {
        // ignore
      }
    })();
    (async () => {
      try {
        const data = await api.get<PipelineRunListResponse>(
          "/api/pipeline-runs?page_size=200"
        );
        setPipelineRuns(
          data.runs.map((r) => ({
            id: r.id,
            label: `${r.pipeline_name || r.pipeline_key || "Run"} #${r.id}`,
          }))
        );
      } catch {
        // ignore
      }
    })();
  }, []);

  const fetchPlots = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
      });
      if (query) params.set("query", query);
      if (experimentId) params.set("experiment_id", experimentId);
      if (pipelineRunId) params.set("pipeline_run_id", pipelineRunId);
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
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
  }, [page, query, experimentId, pipelineRunId, dateFrom, dateTo]);

  useEffect(() => {
    fetchPlots();
  }, [fetchPlots]);

  const resetPage = () => setPage(1);

  const handleExpand = (plot: PlotArchiveResponse, signedUrl: string) => {
    setExpandedUrl(signedUrl);
    setExpandedTitle(plot.title ?? "Plot");
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Plot Archive</h1>

          <div className="space-y-4">
            {/* Filters */}
            <div className="flex flex-wrap gap-3 items-end">
              <div className="flex-1 min-w-[200px]">
                <label className="block text-xs text-gray-500 mb-1">
                  Search
                </label>
                <input
                  type="text"
                  placeholder="Search plots..."
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    resetPage();
                  }}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                />
              </div>
              <div className="min-w-[180px]">
                <label className="block text-xs text-gray-500 mb-1">
                  Experiment
                </label>
                <select
                  value={experimentId}
                  onChange={(e) => {
                    setExperimentId(e.target.value);
                    resetPage();
                  }}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
                >
                  <option value="">All experiments</option>
                  {experiments.map((exp) => (
                    <option key={exp.id} value={exp.id}>
                      {exp.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="min-w-[180px]">
                <label className="block text-xs text-gray-500 mb-1">
                  Pipeline Run
                </label>
                <select
                  value={pipelineRunId}
                  onChange={(e) => {
                    setPipelineRunId(e.target.value);
                    resetPage();
                  }}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
                >
                  <option value="">All runs</option>
                  {pipelineRuns.map((run) => (
                    <option key={run.id} value={run.id}>
                      {run.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">
                  Date from
                </label>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => {
                    setDateFrom(e.target.value);
                    resetPage();
                  }}
                  className="px-3 py-2 border border-gray-300 rounded-md text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">
                  Date to
                </label>
                <input
                  type="date"
                  value={dateTo}
                  onChange={(e) => {
                    setDateTo(e.target.value);
                    resetPage();
                  }}
                  className="px-3 py-2 border border-gray-300 rounded-md text-sm"
                />
              </div>
            </div>

            {loading ? (
              <p className="text-gray-400 text-sm">Loading...</p>
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
                            onClick={(url) => handleExpand(plot, url)}
                          />
                        ) : (
                          <span className="text-gray-400 text-xs">
                            No preview
                          </span>
                        )}
                      </div>
                      <div className="p-2">
                        <p className="text-xs font-medium truncate">
                          {plot.title}
                        </p>
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
        </main>
      </div>
    </div>
  );
}
