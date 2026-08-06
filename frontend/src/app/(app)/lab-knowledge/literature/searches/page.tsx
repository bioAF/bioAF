"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { SearchProgress, sourceChipClass } from "@/components/literature/SearchProgress";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorState } from "@/components/shared/ErrorState";
import { clickableCard } from "@/lib/a11y";

import {
  cleanText,
  formatAuthors,
  formatYear,
  literature,
  type Paper,
  type SearchSummary,
} from "@/lib/literature";

export default function LiteratureSearchesPage() {
  const router = useRouter();
  const [searches, setSearches] = useState<SearchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [activeSearchId, setActiveSearchId] = useState<number | null>(null);
  const [activeResults, setActiveResults] = useState<Paper[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [adding, setAdding] = useState(false);
  const [pollStatus, setPollStatus] = useState<SearchSummary | null>(null);
  const cancelRef = useRef(false);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function refresh() {
    setLoading(true);
    setError(null);
    literature
      .listSearches()
      .then((data) => {
        setSearches(data.items);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load searches."))
      .finally(() => setLoading(false));
  }

  async function submit() {
    if (!query.trim()) return;
    cancelRef.current = false;
    setSubmitting(true);
    try {
      const created = await literature.submitSearch({ query: query.trim() });
      setQuery("");
      setActiveSearchId(created.id);
      // Show progress immediately from the created record, then keep it live.
      setPollStatus(created);
      // Poll per-source status until every source is terminal (or the user stops
      // watching). The count of finished sources is the honest progress signal.
      for (let i = 0; i < 60; i++) {
        if (cancelRef.current) break;
        await new Promise((r) => setTimeout(r, 1000));
        if (cancelRef.current) break;
        const s = await literature.getSearch(created.id);
        if (cancelRef.current) break;
        setPollStatus(s);
        if (
          s.status === "complete" ||
          s.status === "partial" ||
          s.status === "failed"
        ) {
          break;
        }
      }
      if (!cancelRef.current) {
        refresh();
        const results = await literature.getSearchResults(created.id);
        setActiveResults(results.items);
        setSelectedIds(new Set());
      }
    } finally {
      setSubmitting(false);
      setPollStatus(null);
    }
  }

  // Stop the client from waiting. The search keeps running server-side and appears
  // in the list below when it finishes; the poll loop notices the flag and exits.
  function stopWatching() {
    cancelRef.current = true;
    setSubmitting(false);
    setPollStatus(null);
    refresh();
  }

  async function viewResults(id: number) {
    setActiveSearchId(id);
    setSelectedIds(new Set());
    const data = await literature.getSearchResults(id);
    setActiveResults(data.items);
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
      const data = await literature.getSearchResults(activeSearchId!);
      setActiveResults(data.items);
    } finally {
      setAdding(false);
    }
  };

  const addSelected = async () => {
    if (selectedIds.size === 0) return;
    setAdding(true);
    try {
      await literature.bulkAddToLibrary(Array.from(selectedIds));
      const data = await literature.getSearchResults(activeSearchId!);
      setActiveResults(data.items);
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
            <input aria-label="e.g., TGF-beta signalling in triple-negative breast cancer"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g., TGF-beta signalling in triple-negative breast cancer"
              className="flex-1 border border-gray-300 rounded px-3 py-2"
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
            <button
              onClick={submit}
              disabled={submitting || !query.trim()}
              className="bg-bioaf-600 text-white px-4 py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
            >
              {submitting ? "Searching..." : "Search"}
            </button>
          </div>
          <p className="text-xs text-gray-500 mt-2">
            Runs across PubMed, bioRxiv, Europe PMC, and Semantic Scholar in
            parallel. Results do not enter the Library until you add them.
          </p>
        </div>

        {submitting && pollStatus && (
          <SearchProgress status={pollStatus} onStop={stopWatching} />
        )}

        {loading ? (
          <LoadingSpinner />
        ) : error ? (
          <ErrorState
            message="Couldn't load searches."
            details={error}
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
                <button
                  onClick={addSelected}
                  disabled={selectedIds.size === 0 || adding}
                  className="px-3 py-1.5 bg-bioaf-600 text-white rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
                >
                  {adding ? "Adding..." : `Add ${selectedIds.size || ""} to Library`}
                </button>
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
