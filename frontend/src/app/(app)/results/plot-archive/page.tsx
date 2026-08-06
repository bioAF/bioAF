"use client";

import { useState, useEffect, useCallback } from "react";
import { PlotModal } from "@/components/shared/PlotModal";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api, fileContentUrl, plotThumbnailContentUrl } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { PlotThumbnail, StorageDeletedPlaceholder } from "@/components/plots/PlotThumbnail";
import { ErrorState } from "@/components/shared/ErrorState";
import type {
  PlotArchiveResponse,
  PlotArchiveListResponse,
  ExperimentListResponse,
  PipelineRunListResponse,
} from "@/lib/types";

export default function PlotArchivePage() {
  const [plots, setPlots] = useState<PlotArchiveResponse[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
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
  const [expandedPlot, setExpandedPlot] = useState<PlotArchiveResponse | null>(null);
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
    } catch (e) {
      logError("loading the plot archive", e);
      setLoadError(loadFailureMessage("Plots"));
    } finally {
      setLoading(false);
    }
  }, [page, query, experimentId, pipelineRunId, dateFrom, dateTo]);

  useEffect(() => {
    fetchPlots();
  }, [fetchPlots]);

  const resetPage = () => setPage(1);

  const handleExpand = async (plot: PlotArchiveResponse) => {
    const isPdf = plot.file?.file_type?.toLowerCase() === "pdf";
    const url = isPdf && plot.thumbnail_url
      ? await plotThumbnailContentUrl(plot.id)
      : plot.file
        ? await fileContentUrl(plot.file.id)
        : "";
    setExpandedUrl(url);
    setExpandedTitle(plot.title ?? "Plot");
    setExpandedPlot(plot);
  };

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-6">Plot Archive</h1>

      <div className="space-y-4">
        {/* Filters */}
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-[200px]">
            <label htmlFor="search" className="block text-xs text-gray-500 mb-1">
              Search
            </label>
            <input id="search"
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
            <label htmlFor="experiment" className="block text-xs text-gray-500 mb-1">
              Experiment
            </label>
            <select id="experiment"
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
            <label htmlFor="pipeline-run" className="block text-xs text-gray-500 mb-1">
              Pipeline Run
            </label>
            <select id="pipeline-run"
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
            <label htmlFor="date-from" className="block text-xs text-gray-500 mb-1">
              Date from
            </label>
            <input id="date-from"
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
            <label htmlFor="date-to" className="block text-xs text-gray-500 mb-1">
              Date to
            </label>
            <input id="date-to"
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
          <ContentLoading />
        ) : loadError ? (
          <ErrorState message={loadError} onRetry={() => fetchPlots()} />
        ) : plots.length === 0 ? (
          <p className="text-gray-500 text-sm">No plots found.</p>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
              {plots.map((plot) => {
                const deleted = plot.file?.storage_deleted === true;
                return (
                  <div
                    key={plot.id}
                    className={`bg-white rounded-lg shadow overflow-hidden transition-shadow ${deleted ? "opacity-60" : "hover:shadow-md"}`}
                  >
                    <div className="aspect-square bg-gray-100 flex items-center justify-center relative">
                      {deleted ? (
                        <StorageDeletedPlaceholder />
                      ) : plot.file ? (
                        <PlotThumbnail
                          plot={plot}
                          onClick={() => handleExpand(plot)}
                        />
                      ) : (
                        <span className="text-gray-500 text-xs">
                          No preview
                        </span>
                      )}
                      {plot.file && (
                        <span className="absolute top-1.5 right-1.5 px-1.5 py-0.5 bg-black/50 text-white text-[10px] font-semibold uppercase rounded">
                          {plot.file.file_type}
                        </span>
                      )}
                    </div>
                    <div className="p-2">
                      <p className={`text-[11px] leading-tight font-medium line-clamp-2 ${deleted ? "text-gray-500" : ""}`} title={plot.title ?? undefined}>
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
                );
              })}
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
    </main>
  );
}
