"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { SearchProgress, sourceChipClass } from "@/components/literature/SearchProgress";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorState } from "@/components/shared/ErrorState";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { clickableCard } from "@/lib/a11y";
import { Button } from "@/components/ui/Button";
import { StaleNotice } from "@/components/shared/StaleNotice";
import { useResource } from "@/hooks/useResource";
import { isAbortError } from "@/lib/abort";
import { watchJob } from "@/lib/watchJob";

import {  cleanText,
  formatAuthors,
  formatYear,
  literature,
  type Paper,
  type SearchSummary,
} from "@/lib/literature";

export default function LiteratureSearchesPage() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [activeSearchId, setActiveSearchId] = useState<number | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [adding, setAdding] = useState(false);
  const [pollStatus, setPollStatus] = useState<SearchSummary | null>(null);

  const list = useResource(
    "literature-searches",
    useCallback(() => literature.listSearches(), []),
    { onError: (e) => logError("loading searches", e) },
  );
  const searches: SearchSummary[] = list.data?.items ?? [];
  const loading = list.loading;
  const error = list.error && !list.stale ? loadFailureMessage("Searches") : null;
  const refresh = () => void list.refresh();

  // The results shown belong to the selected search only: a late answer for an
  // earlier selection is dropped.
  // A search being watched shows no results until the watch ends (as before).
  const [watchingId, setWatchingId] = useState<number | null>(null);
  const results = useResource(
    activeSearchId === null || activeSearchId === watchingId ? null : `search-results:${activeSearchId}`,
    useCallback(
      () => literature.getSearchResults(activeSearchId as number),
      [activeSearchId],
    ),
    { onError: (e) => logError(`loading results for search ${activeSearchId}`, e) },
  );
  // A failed load is said; a failed refresh (e.g. after Add to Library) keeps the results
  // visible but marked out of date, since their Add buttons may no longer be right.
  const resultsError = results.error && !results.stale ? loadFailureMessage("Search results") : null;
  const activeResults: Paper[] = results.data?.items ?? [];

  // The search being watched, if any. A new submission, Stop watching, or leaving
  // the page ends that watch (never the search itself).
  const watchRef = useRef<AbortController | null>(null);
  useEffect(() => () => watchRef.current?.abort(), []);

  async function submit() {
    if (!query.trim()) return;
    watchRef.current?.abort();
    const controller = new AbortController();
    watchRef.current = controller;
    setSubmitting(true);
    try {
      const created = await literature.submitSearch({ query: query.trim() });
      if (controller.signal.aborted) return;
      setQuery("");
      setWatchingId(created.id);
      setActiveSearchId(created.id);
      setSelectedIds(new Set());
      // Show progress immediately from the created record, then keep it live.
      setPollStatus(created);
      // Poll per-source status until every source is terminal (at most 60 reads, one
      // second apart). The count of finished sources is the honest progress signal.
      await watchJob(
        created,
        async () => {
          const s = await literature.getSearch(created.id);
          if (!controller.signal.aborted) setPollStatus(s);
          return s;
        },
        (s) => s !== created && (s.status === "complete" || s.status === "partial" || s.status === "failed"),
        { intervalMs: 1000, maxReads: 60, signal: controller.signal },
      );
      // Ending the watch lets the finished search's results load, but only if the
      // user is still on it: a selection made meanwhile is left alone.
      refresh();
    } catch (e) {
      if (!isAbortError(e) && !controller.signal.aborted) throw e;
    } finally {
      if (watchRef.current === controller) {
        watchRef.current = null;
        setWatchingId(null);
        setSubmitting(false);
        setPollStatus(null);
      }
    }
  }

  // Stop the client from waiting. The search keeps running server-side and appears
  // in the list below when it finishes.
  function stopWatching() {
    watchRef.current?.abort();
    watchRef.current = null;
    setWatchingId(null);
    setSubmitting(false);
    setPollStatus(null);
    refresh();
  }

  function viewResults(id: number) {
    setSelectedIds(new Set());
    if (id === activeSearchId) {
      void results.refresh();
      return;
    }
    setActiveSearchId(id);
  }

  const toggleSelect = (id: number) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleSelectAll = () => {
    const eligible = activeResults.filter((p) => !p.in_library).map((p) => p.id);
    if (selectedIds.size === eligible.length) setSelectedIds(new Set());
    else setSelectedIds(new Set(eligible));
  };

  const addOne = async (id: number) => {
    setAdding(true);
    try {
      await literature.addToLibrary(id);
      await results.refresh();
    } finally {
      setAdding(false);
    }
  };

  const addSelected = async () => {
    if (selectedIds.size === 0) return;
    setAdding(true);
    try {
      await literature.bulkAddToLibrary(Array.from(selectedIds));
      await results.refresh();
      setSelectedIds(new Set());
    } finally {
      setAdding(false);
    }
  };

  return (
    <>
      <Breadcrumb entityName="Searches" />
      <main className="flex-1 overflow-y-auto p-6">
        <button
          onClick={() => router.push("/lab-knowledge/literature")}
          className="text-bioaf-700 hover:underline text-sm mb-4"
        >
          &larr; Back to library
        </button>
        <h1 className="text-2xl font-bold mb-6">Literature Searches</h1>
        <div className="bg-white rounded shadow p-4 mb-6">
          <div className="flex gap-2">
            {/* The placeholder is an example query, not a label, so it stays
                visible but does not become the accessible name. */}
            <input aria-label="Literature Search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g., TGF-beta signalling in triple-negative breast cancer"
              className="flex-1 border border-gray-300 rounded px-3 py-2"
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
            <Button
              onClick={submit}
              disabled={submitting || !query.trim()}>
              {submitting ? "Searching..." : "Search"}
            </Button>
          </div>
          <p className="text-xs text-gray-500 mt-2">
            Runs across PubMed, bioRxiv, Europe PMC, and Semantic Scholar in
            parallel. Results do not enter the Library until you add them.
          </p>
        </div>

        {submitting && pollStatus && (
          <SearchProgress status={pollStatus} onStop={stopWatching} />
        )}

        {list.stale && <StaleNotice what="Searches" onRetry={refresh} />}
        {loading ? (
          <LoadingSpinner />
        ) : error ? (
          <ErrorState
            message={error}
            onRetry={refresh}
          />
        ) : (
          <div className="bg-white rounded shadow divide-y">
            {searches.length === 0 ? (
              <div className="p-6 text-sm text-gray-500">No searches yet.</div>
            ) : (
              searches.map((s) => (
                <div
                  key={s.id}
                  className={`p-4 cursor-pointer ${activeSearchId === s.id ? "bg-bioaf-50" : "hover:bg-gray-50"}`}
                  {...clickableCard(() => viewResults(s.id))}
                >
                  <div className="flex justify-between">
                    <div className="font-mono text-sm">{s.query_text}</div>
                    <div className="text-xs text-gray-500">
                      {new Date(s.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div className="text-xs text-gray-600 mt-1">
                    status: <span className="font-medium">{s.status}</span>
                    {s.result_count !== null && ` · ${s.result_count} results`}
                  </div>
                  <div className="flex flex-wrap gap-2 mt-2">
                    {Object.entries(s.per_source_status).map(([source, st]) => (
                      <span key={source} className={sourceChipClass(st)}>
                        {source}: {st}
                      </span>
                    ))}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {resultsError && (
          <div
            data-testid="search-results-error"
            role="status"
            className="mt-6 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800 flex items-center justify-between gap-3"
          >
            <span>{resultsError}</span>
            <Button variant="secondary" size="sm" onClick={() => void results.refresh()}>
              Retry
            </Button>
          </div>
        )}

        {results.stale && (
          <div className="mt-6">
            <StaleNotice what="Search results" onRetry={() => void results.refresh()} />
          </div>
        )}

        {activeResults.length > 0 && (
          <div className="mt-6 bg-white rounded shadow p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-semibold">Results</h2>
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={
                      activeResults.filter((p) => !p.in_library).length > 0 &&
                      selectedIds.size ===
                        activeResults.filter((p) => !p.in_library).length
                    }
                    onChange={toggleSelectAll}
                  />
                  <span>Select all not-in-library</span>
                </label>
                <Button size="sm"
                  onClick={addSelected}
                  disabled={selectedIds.size === 0 || adding}>
                  {adding ? "Adding..." : `Add ${selectedIds.size || ""} to Library`}
                </Button>
              </div>
            </div>
            <ul className="divide-y">
              {activeResults.map((p) => (
                <li key={p.id} className="py-3 flex gap-3">
                  {p.in_library ? (
                    <span
                      title="Already in Library"
                      className="w-5 h-5 flex items-center justify-center mt-1 rounded-full bg-emerald-100 text-emerald-700 text-xs"
                    >
                      &#10003;
                    </span>
                  ) : (
                    <input
                      type="checkbox"
                      aria-label={`Select ${cleanText(p.title)}`}
                      className="mt-1.5"
                      checked={selectedIds.has(p.id)}
                      onChange={() => toggleSelect(p.id)}
                    />
                  )}
                  <div className="flex-1">
                    <button
                      onClick={() =>
                        router.push(`/lab-knowledge/literature/papers/${p.id}`)
                      }
                      className="text-bioaf-700 hover:underline text-left font-medium"
                    >
                      {cleanText(p.title)}
                    </button>
                    <div className="text-sm text-gray-600">
                      {formatAuthors(p.authors)} &middot;{" "}
                      {formatYear(p.publication_date)} &middot;{" "}
                      {cleanText(p.journal)}
                    </div>
                    {p.abstract && (
                      <div className="text-sm text-gray-700 mt-1 line-clamp-3">
                        {cleanText(p.abstract)}
                      </div>
                    )}
                    <div className="text-xs mt-2 flex gap-2 items-center">
                      {p.in_library ? (
                        <span className="text-emerald-700">In Library</span>
                      ) : (
                        <button
                          disabled={adding}
                          onClick={() => addOne(p.id)}
                          className="text-bioaf-600 hover:underline"
                        >
                          Add to Library
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </main>
    </>
  );
}
