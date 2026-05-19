"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import {
  literature,
  type Paper,
  type PaperFilters,
  type Provenance,
  formatAuthors,
  formatYear,
} from "@/lib/literature";

const provenanceLabels: Record<Provenance, string> = {
  user_upload: "Uploaded",
  source_search: "From search",
  lit_review_run: "Lit Review",
};

const provenanceBadgeColors: Record<Provenance, string> = {
  user_upload: "bg-blue-100 text-blue-800",
  source_search: "bg-green-100 text-green-800",
  lit_review_run: "bg-purple-100 text-purple-800",
};

export default function LiteratureLibraryPage() {
  const router = useRouter();
  const user = getCurrentUser();
  const canUpload =
    user?.role_name === "admin" ||
    user?.role_name === "comp_bio" ||
    user?.role_name === "bench";

  const [papers, setPapers] = useState<Paper[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [provenance, setProvenance] = useState<Provenance | "">("");
  const [showDismissed, setShowDismissed] = useState(false);
  const [sort, setSort] = useState<"added" | "title" | "year" | "comments">(
    "added",
  );

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
  }, [router]);

  useEffect(() => {
    if (!isAuthenticated()) return;
    setLoading(true);
    const filters: PaperFilters = {
      sort,
      show_dismissed: showDismissed,
      page: 1,
      page_size: 50,
    };
    if (provenance) filters.provenance = provenance;
    literature
      .listPapers(filters)
      .then((data) => {
        setPapers(data.items);
        setTotal(data.total);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [provenance, showDismissed, sort]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Literature Library</h1>
            <div className="flex gap-2">
              <button
                onClick={() => router.push("/data/literature/recommendations")}
                className="border border-gray-300 px-4 py-2 rounded-md hover:bg-gray-50"
              >
                Recommendations
              </button>
              <button
                onClick={() => router.push("/data/literature/searches")}
                className="border border-gray-300 px-4 py-2 rounded-md hover:bg-gray-50"
              >
                Searches
              </button>
              <button
                onClick={() => router.push("/data/literature/sources")}
                className="border border-gray-300 px-4 py-2 rounded-md hover:bg-gray-50"
              >
                Sources
              </button>
              {canUpload && (
                <button
                  onClick={() => router.push("/data/literature/upload")}
                  className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700"
                >
                  Upload paper
                </button>
              )}
            </div>
          </div>

          <div className="flex flex-wrap gap-4 mb-4 items-end">
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                Provenance
              </label>
              <select
                value={provenance}
                onChange={(e) => setProvenance(e.target.value as Provenance | "")}
                className="border border-gray-300 rounded px-3 py-2"
              >
                <option value="">All</option>
                <option value="user_upload">Uploaded by humans</option>
                <option value="source_search">From searches</option>
                <option value="lit_review_run">From Lit Review</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-600 mb-1">Sort</label>
              <select
                value={sort}
                onChange={(e) =>
                  setSort(
                    e.target.value as
                      | "added"
                      | "title"
                      | "year"
                      | "comments",
                  )
                }
                className="border border-gray-300 rounded px-3 py-2"
              >
                <option value="added">Date added</option>
                <option value="title">Title</option>
                <option value="year">Year</option>
                <option value="comments">Comment count</option>
              </select>
            </div>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={showDismissed}
                onChange={(e) => setShowDismissed(e.target.checked)}
              />
              <span className="text-sm">Show dismissed</span>
            </label>
            <span className="ml-auto text-sm text-gray-500">{total} papers</span>
          </div>

          {loading ? (
            <LoadingSpinner />
          ) : papers.length === 0 ? (
            <div className="border border-dashed border-gray-300 rounded p-12 text-center text-gray-500">
              No papers yet. Use Upload, Search, or run a Lit Review for an
              experiment to populate the library.
            </div>
          ) : (
            <div className="bg-white rounded shadow overflow-hidden">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      Title
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      Authors
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      Year
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      Journal
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      Provenance
                    </th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                      Comments
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-100">
                  {papers.map((p) => (
                    <tr
                      key={p.id}
                      className="hover:bg-gray-50 cursor-pointer"
                      onClick={() =>
                        router.push(`/data/literature/papers/${p.id}`)
                      }
                    >
                      <td className="px-4 py-2 text-sm font-medium text-bioaf-700 max-w-md truncate">
                        {p.title}
                        {p.dismissed && (
                          <span className="ml-2 inline-block px-2 py-0.5 text-xs rounded bg-red-100 text-red-700">
                            dismissed
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-sm text-gray-600 max-w-xs truncate">
                        {formatAuthors(p.authors)}
                      </td>
                      <td className="px-4 py-2 text-sm text-gray-600">
                        {formatYear(p.publication_date)}
                      </td>
                      <td className="px-4 py-2 text-sm text-gray-600 max-w-xs truncate">
                        {p.journal ?? ""}
                      </td>
                      <td className="px-4 py-2 text-sm">
                        <span
                          className={`inline-block px-2 py-0.5 rounded text-xs ${provenanceBadgeColors[p.provenance]}`}
                        >
                          {provenanceLabels[p.provenance]}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-sm text-gray-600">
                        {p.comment_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
