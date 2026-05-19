"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { isAuthenticated } from "@/lib/auth";
import {
  literature,
  type Paper,
  type SearchSummary,
  formatAuthors,
  formatYear,
} from "@/lib/literature";

export default function LiteratureSearchesPage() {
  const router = useRouter();
  const [searches, setSearches] = useState<SearchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [activeSearchId, setActiveSearchId] = useState<number | null>(null);
  const [activeResults, setActiveResults] = useState<Paper[]>([]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function refresh() {
    setLoading(true);
    literature
      .listSearches()
      .then((data) => setSearches(data.items))
      .finally(() => setLoading(false));
  }

  async function submit() {
    if (!query.trim()) return;
    setSubmitting(true);
    try {
      const created = await literature.submitSearch({ query: query.trim() });
      setQuery("");
      setActiveSearchId(created.id);
      // Poll briefly for status updates.
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const s = await literature.getSearch(created.id);
        if (s.status === "complete" || s.status === "partial" || s.status === "failed") {
          break;
        }
      }
      refresh();
      const results = await literature.getSearchResults(created.id);
      setActiveResults(results.items);
    } finally {
      setSubmitting(false);
    }
  }

  async function viewResults(id: number) {
    setActiveSearchId(id);
    const data = await literature.getSearchResults(id);
    setActiveResults(data.items);
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <button
            onClick={() => router.push("/data/literature")}
            className="text-bioaf-700 hover:underline text-sm mb-4"
          >
            ← Back to library
          </button>
          <h1 className="text-2xl font-bold mb-6">Literature Searches</h1>
          <div className="bg-white rounded shadow p-4 mb-6">
            <div className="flex gap-2">
              <input
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
              Runs across PubMed, bioRxiv, Europe PMC, and Semantic Scholar in parallel.
            </p>
          </div>

          {loading ? (
            <LoadingSpinner />
          ) : (
            <div className="bg-white rounded shadow divide-y">
              {searches.length === 0 ? (
                <div className="p-6 text-sm text-gray-500">No searches yet.</div>
              ) : (
                searches.map((s) => (
                  <div
                    key={s.id}
                    className={`p-4 cursor-pointer ${activeSearchId === s.id ? "bg-bioaf-50" : "hover:bg-gray-50"}`}
                    onClick={() => viewResults(s.id)}
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
                        <span
                          key={source}
                          className={
                            st === "complete"
                              ? "px-2 py-0.5 text-xs rounded bg-green-100 text-green-700"
                              : st.startsWith("failed")
                                ? "px-2 py-0.5 text-xs rounded bg-red-100 text-red-700"
                                : "px-2 py-0.5 text-xs rounded bg-gray-100 text-gray-700"
                          }
                        >
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
              <h2 className="font-semibold mb-3">Results</h2>
              <ul className="divide-y">
                {activeResults.map((p) => (
                  <li key={p.id} className="py-3">
                    <button
                      onClick={() => router.push(`/data/literature/papers/${p.id}`)}
                      className="text-bioaf-700 hover:underline text-left font-medium"
                    >
                      {p.title}
                    </button>
                    <div className="text-sm text-gray-600">
                      {formatAuthors(p.authors)} · {formatYear(p.publication_date)} ·{" "}
                      {p.journal ?? ""}
                    </div>
                    {p.abstract && (
                      <div className="text-sm text-gray-700 mt-1 line-clamp-3">
                        {p.abstract}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
