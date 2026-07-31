"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { searchHitTypeLabel } from "@/lib/searchLinks";
import type { SearchHit, SearchResult } from "@/lib/types";

const PAGE_SIZE = 25;

// Stable display order for the type filter (matches the backend's universe).
const TYPE_ORDER = [
  "experiment",
  "sample",
  "pipeline_run",
  "file",
  "project",
  "pipeline_definition",
  "literature_paper",
];

export default function SearchPage() {
  return (
    <Suspense fallback={null}>
      <SearchPageInner />
    </Suspense>
  );
}

function SearchPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialQuery = searchParams?.get("q") ?? "";

  const [query, setQuery] = useState(initialQuery);
  const [selectedType, setSelectedType] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<SearchResult | null>(null);
  const [typeCounts, setTypeCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  const debounced = useDebouncedValue(query, 300);
  const term = debounced.trim();

  useEffect(() => {
    if (!term) {
      setData(null);
      setError(false);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(false);
    const params = new URLSearchParams({
      query: term,
      page: String(page),
      page_size: String(PAGE_SIZE),
    });
    if (selectedType) params.set("entity_types", selectedType);
    api
      .get<SearchResult>(`/api/search?${params.toString()}`)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setTypeCounts(res.type_counts || {});
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [term, selectedType, page, reload]);

  const onQueryChange = (v: string) => {
    setQuery(v);
    setPage(1);
  };
  const onTypeChange = (v: string) => {
    setSelectedType(v);
    setPage(1);
  };

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const firstShown = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const lastShown = Math.min(page * PAGE_SIZE, total);
  const availableTypes = TYPE_ORDER.filter((t) => t in typeCounts);
  const allCount = Object.values(typeCounts).reduce((a, b) => a + b, 0);

  return (
    <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-1">Search</h1>
          <p className="text-sm text-gray-500 mb-4">
            Results across experiments, samples, runs, files, projects, pipelines, and papers.
          </p>

          <div className="flex flex-wrap items-center gap-3 mb-4">
            <input
              type="search"
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              aria-label="Search"
              placeholder="Search everything..."
              className="flex-1 min-w-64 px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-1 focus:ring-bioaf-500"
            />
            <select
              value={selectedType}
              onChange={(e) => onTypeChange(e.target.value)}
              aria-label="Filter by type"
              className="px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
            >
              <option value="">All types{term ? ` (${allCount})` : ""}</option>
              {availableTypes.map((t) => (
                <option key={t} value={t}>
                  {searchHitTypeLabel(t)} ({typeCounts[t]})
                </option>
              ))}
            </select>
          </div>

          {!term ? (
            <div className="text-sm text-gray-500 py-12 text-center">Enter a search term to begin.</div>
          ) : error ? (
            <div className="py-12 text-center">
              <p className="text-sm text-red-600 mb-3">Something went wrong loading results.</p>
              <button
                onClick={() => setReload((n) => n + 1)}
                className="px-3 py-2 text-sm border border-gray-300 rounded-md hover:bg-gray-100"
              >
                Retry
              </button>
            </div>
          ) : loading && !data ? (
            <div className="text-sm text-gray-500 py-12 text-center">Searching...</div>
          ) : total === 0 ? (
            <div className="text-sm text-gray-500 py-12 text-center">No results for &ldquo;{term}&rdquo;.</div>
          ) : (
            <>
              <div className="text-xs text-gray-500 mb-2">
                Showing {firstShown}-{lastShown} of {total}
                {total >= 300 ? "+" : ""}
              </div>
              <ul className="space-y-2">
                {(data?.results ?? []).map((hit) => (
                  <ResultCard key={`${hit.entity_type}-${hit.entity_id}`} hit={hit} onOpen={() => router.push(hit.url)} />
                ))}
              </ul>
              <div className="flex items-center justify-between mt-4">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-40"
                >
                  Previous
                </button>
                <span className="text-xs text-gray-500">
                  Page {page} of {totalPages}
                </span>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                  className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </>
          )}
        </main>
  );
}

function ResultCard({ hit, onOpen }: { hit: SearchHit; onOpen: () => void }) {
  return (
    <li>
      <button
        onClick={onOpen}
        className="w-full flex items-start gap-3 text-left bg-white border border-gray-200 rounded-lg p-3 hover:bg-gray-50 hover:border-gray-300"
      >
        <span className="mt-0.5 text-[10px] uppercase tracking-wide bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded flex-shrink-0">
          {searchHitTypeLabel(hit.entity_type)}
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-medium text-gray-900 truncate">{hit.title}</span>
          {hit.snippet ? <span className="block text-xs text-gray-500 truncate">{hit.snippet}</span> : null}
        </span>
      </button>
    </li>
  );
}
