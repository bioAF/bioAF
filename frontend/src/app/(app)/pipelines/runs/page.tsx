"use client";

import { NOT_SET } from "@/lib/placeholders";
import { NotSet } from "@/components/shared/NotSet";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { PageSizeSelect, DEFAULT_PAGE_SIZE } from "@/components/shared/PageSizeSelect";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api } from "@/lib/api";
import { useCapabilities } from "@/hooks/useCapabilities";
import { ReviewBadge } from "@/components/experiments/ReviewBadge";
import { statusBadgeClass } from "@/lib/statusStyles";
import type { PipelineRun, PipelineRunListResponse } from "@/lib/types";

import { clickableRow } from "@/lib/a11y";
import { logError, loadFailureMessage } from "@/lib/errorReporting";

/** Matches the run detail page, which is the surface a user flips to from here. */
const REFRESH_INTERVAL_MS = 10_000;
/** The duration column shows seconds under a minute, so it needs a per-second clock. */
const CLOCK_TICK_MS = 1_000;

export default function PipelineRunsPage() {
  const router = useRouter();
  const { has } = useCapabilities();
  const showCost = has("cost_estimation");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);
  // Unset until the user asks, so an untouched list keeps the server's own
  // ordering (created_at DESC) rather than being silently re-sorted by id.
  const [sortField, setSortField] = useState<"id" | "status" | "pipeline_name" | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    loadRuns();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, page, pageSize, statusFilter, sortField, sortDir]);

  // This is the fleet view, and it was the only live surface in the app that never
  // refreshed itself: the run detail polls every 10s, logs every 5s, pipeline
  // templates every 5s, cellxgene every 5s. A run that finished, failed, or was
  // launched from anywhere else did not appear here until the user reloaded.
  useEffect(() => {
    const interval = setInterval(() => loadRuns({ silent: true }), REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, statusFilter, sortField, sortDir]);

  // `formatDuration` read Date.now() once, at render, so an in-flight 8-hour run read
  // "3m" until the page was reloaded. It is the only elapsed-time signal on this
  // screen, so it has to be driven by a clock rather than by whenever React last
  // happened to re-render. Only ticks while something is actually in flight.
  const hasInFlight = runs.some((r) => !r.completed_at && r.started_at);
  useEffect(() => {
    if (!hasInFlight) return;
    const tick = setInterval(() => setNowMs(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(tick);
  }, [hasInFlight]);

  async function loadRuns(opts: { silent?: boolean } = {}) {
    // A background refresh must not replace the table with a skeleton every 10s.
    if (!opts.silent) setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
      if (statusFilter) params.set("status", statusFilter);
      // Sorting happens in the query, before the LIMIT. Sorting the rows this
      // page already holds answers a different question: on the demo, "sort by
      // ID ascending" over page 1 of 29 runs put #5 at the top when the real
      // answer was #1, on page 2.
      if (sortField) {
        params.set("sort_by", sortField);
        params.set("sort_dir", sortDir);
      }
      const data = await api.get<PipelineRunListResponse>(`/api/pipeline-runs?${params}`);
      setRuns(data.runs);
      setTotal(data.total);
      setLoadError(null);
    } catch (e) {
      // Two things were wrong here. The raw server string went onto the screen
      // ("Could not load pipeline runs. {loadError}"), against the house rule. And
      // `runs` was never touched, so the red error row rendered UNDERNEATH a full table
      // of stale rows that still read as current.
      //
      // The rows are not discarded, because a transient blip during a 10s poll should
      // not blank a table the user is reading. They are labelled instead: the banner
      // sits above the table and says what is shown may be out of date.
      logError("loading the pipeline runs list", e);
      setLoadError(loadFailureMessage("The pipeline runs list"));
    } finally { if (!opts.silent) setLoading(false); }
  }

  function formatDuration(startedAt: string | null, completedAt: string | null): string {
    if (!startedAt) return NOT_SET;
    const start = new Date(startedAt).getTime();
    const end = completedAt ? new Date(completedAt).getTime() : nowMs;
    const seconds = Math.floor((end - start) / 1000);
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }

  function formatDateTime(dateStr: string | null): string {
    if (!dateStr) return NOT_SET;
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) + " " +
      d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  }

  function toggleSort(field: "id" | "status" | "pipeline_name") {
    if (sortField === field) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortField(field);
      setSortDir("desc");
    }
    // Re-sorting the whole list means page 4 of the old order is meaningless.
    setPage(1);
  }

  // Deliberately NOT re-sorted here. The rows arrive in the order the server
  // was asked for, and reordering them again would reinstate the defect: a
  // client can only ever sort what it already has.
  const sortedRuns = runs;

  const sortIcon = (field: string) => sortField === field ? (sortDir === "desc" ? " ↓" : " ↑") : "";

  // Sortable headers were mouse-only, and the sort direction was conveyed
  // solely by the arrow glyph above. `aria-sort` is the machine-readable
  // equivalent, so the column announces "sorted descending" rather than leaving
  // a screen reader to guess from a character appended to the label.
  const sortProps = (field: "id" | "status" | "pipeline_name") => ({
    ...clickableRow(() => toggleSort(field)),
    "aria-sort": (sortField === field
      ? sortDir === "desc"
        ? "descending"
        : "ascending"
      : "none") as "descending" | "ascending" | "none",
  });

  return (
    <main className="flex-1 overflow-y-auto p-6">
      {loading ? (
        <ContentLoading variant="table" />
      ) : (
      <>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Pipeline Runs</h1>
          <p data-testid="page-description" className="text-sm text-ink-subtle mt-1">
            Every pipeline execution, with its status, progress and review verdict.
          </p>
        </div>
        <select aria-label="Filter by status" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }} className="border rounded-md px-3 py-1.5 text-sm">
          <option value="">All statuses</option>
          <option value="running">Running</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="pending">Pending</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      {loadError && (
        <div
          data-testid="runs-load-error"
          role="status"
          className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800 flex items-center justify-between gap-3"
        >
          <span>
            {loadError}
            {runs.length > 0 && " The runs below are from the last successful refresh and may be out of date."}
          </span>
          <Button variant="secondary" size="sm" onClick={() => loadRuns()}>
            Retry
          </Button>
        </div>
      )}

      <div className="bg-surface rounded-lg shadow overflow-x-auto">
        <table className="min-w-full divide-y divide-hairline">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase cursor-pointer select-none hover:text-ink-muted" {...sortProps("id")}>Run{sortIcon("id")}</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase cursor-pointer select-none hover:text-ink-muted" {...sortProps("pipeline_name")}>Pipeline{sortIcon("pipeline_name")}</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Experiment</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase cursor-pointer select-none hover:text-ink-muted" {...sortProps("status")}>Status{sortIcon("status")}</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Review</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Progress</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Submitter</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Started</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Duration</th>
              {showCost && <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Est. $/hr</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {sortedRuns.map((r) => (
              <tr key={r.id} className="hover:bg-surface-muted cursor-pointer" {...clickableRow(() => router.push(`/pipelines/runs/${r.id}`))}>
                <td className="px-4 py-3 text-sm font-mono">#{r.id}</td>
                <td className="px-4 py-3 text-sm">{r.pipeline_name}</td>
                <td className="px-4 py-3 text-sm">{r.experiment?.name || NOT_SET}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 text-xs rounded-full ${statusBadgeClass("pipelineRun", r.status)}`}>{r.status}</span>
                  {r.status === "failed" && r.failure_reason === "oom" && (
                    <span className="ml-1 px-1.5 py-0.5 text-[10px] font-medium rounded bg-orange-100 text-orange-700">OOM</span>
                  )}
                  {r.status === "failed" && r.failure_reason === "preemption_exhausted" && (
                    <span className="ml-1 px-1.5 py-0.5 text-[10px] font-medium rounded bg-blue-100 text-blue-700">Preempted</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  {r.review_verdict ? <ReviewBadge verdict={r.review_verdict} /> : <NotSet />}
                </td>
                <td className="px-4 py-3">
                  {r.progress ? (
                    <div className="flex items-center gap-2">
                      <div className="w-16 h-2 bg-gray-200 rounded-full overflow-hidden">
                        <div className="h-full bg-bioaf-500 rounded-full" style={{ width: `${r.progress.percent_complete}%` }} />
                      </div>
                      <span className="text-xs text-ink-subtle">{Math.round(r.progress.percent_complete)}%</span>
                    </div>
                  ) : <NotSet />}
                </td>
                <td className="px-4 py-3 text-sm">{r.submitted_by?.name || r.submitted_by?.email || NOT_SET}</td>
                <td className="px-4 py-3 text-sm text-ink-subtle">{formatDateTime(r.started_at)}</td>
                <td className="px-4 py-3 text-sm text-ink-subtle">{formatDuration(r.started_at, r.completed_at)}</td>
                {showCost && <td className="px-4 py-3 text-sm text-ink-subtle">{r.cost_estimate ? `$${r.cost_estimate.toFixed(2)}/hr` : NOT_SET}</td>}
              </tr>
            ))}
            {/*
              "No pipeline runs" is only said when the last load SUCCEEDED and returned
              nothing. With an error outstanding, the emptiness is unexplained rather
              than a fact, and the banner above the table is what says so.
            */}
            {!loadError && runs.length === 0 ? (
              <tr><td colSpan={showCost ? 10 : 9} className="px-4 py-12 text-center text-ink-subtle">No pipeline runs</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {/* The size control renders whether or not there is a second page, so it
          is the same control in the same place regardless of how much data the
          list happens to hold. */}
      <div className="flex flex-wrap items-center justify-between gap-3 mt-4">
        <PageSizeSelect
          value={pageSize}
          onChange={(size) => {
            setPageSize(size);
            // Page 12 of 25-row pages does not exist at 100 a page, so keeping
            // the number would show an empty table for a list that is not empty.
            setPage(1);
          }}
        />
        {total > pageSize && (
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={() => setPage(Math.max(1, page - 1))} disabled={page === 1}>Previous</Button>
            <span className="text-sm text-ink-subtle py-1">Page {page} of {Math.ceil(total / pageSize)}</span>
            <Button variant="secondary" size="sm" onClick={() => setPage(page + 1)} disabled={page >= Math.ceil(total / pageSize)}>Next</Button>
          </div>
        )}
      </div>
      </>
      )}
    </main>
  );
}
