"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ReferenceStatusBadge } from "@/components/references/ReferenceStatusBadge";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import type { ReferenceDataset, ReferenceDatasetListResponse } from "@/lib/types";

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function DataReferencesPage() {
  const router = useRouter();
  const user = getCurrentUser();
  const canAdd = user?.role_name === "admin" || user?.role_name === "comp_bio";

  const [references, setReferences] = useState<ReferenceDataset[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [scopeFilter, setScopeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const categories = ["genome", "transcriptome", "annotation", "index", "other"];
  const scopes = ["global", "organization"];
  const statuses = ["active", "deprecated", "pending_approval"];

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
  }, [router]);

  useEffect(() => {
    if (!isAuthenticated()) return;
    setLoading(true);

    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (categoryFilter) params.set("category", categoryFilter);
    if (scopeFilter) params.set("scope", scopeFilter);
    if (statusFilter) params.set("status", statusFilter);

    const query = params.toString();
    api
      .get<ReferenceDatasetListResponse>(`/api/references${query ? `?${query}` : ""}`)
      .then((data) => {
        setReferences(data.references);
        setTotal(data.total);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [search, categoryFilter, scopeFilter, statusFilter]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Reference Data</h1>
            {canAdd && (
              <div className="flex gap-2">
                <button
                  onClick={() => router.push("/data/references/import")}
                  className="border border-bioaf-600 text-bioaf-700 px-4 py-2 rounded-md hover:bg-bioaf-50 transition-colors"
                >
                  Import from URL
                </button>
                <button
                  onClick={() => router.push("/data/references/new")}
                  className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700 transition-colors"
                >
                  Add Reference
                </button>
              </div>
            )}
          </div>

          <div className="bg-white rounded-lg shadow mb-6 p-4">
            <div className="flex flex-wrap gap-4">
              <input
                type="text"
                placeholder="Search by name..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm flex-1 min-w-[200px]"
              />
              <select
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                <option value="">All Categories</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c.charAt(0).toUpperCase() + c.slice(1)}
                  </option>
                ))}
              </select>
              <select
                value={scopeFilter}
                onChange={(e) => setScopeFilter(e.target.value)}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                <option value="">All Scopes</option>
                {scopes.map((s) => (
                  <option key={s} value={s}>
                    {s.charAt(0).toUpperCase() + s.slice(1)}
                  </option>
                ))}
              </select>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="border border-gray-300 rounded-md px-3 py-2 text-sm"
              >
                <option value="">All Statuses</option>
                {statuses.map((s) => (
                  <option key={s} value={s}>
                    {s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {loading ? (
            <div className="flex justify-center py-12">
              <LoadingSpinner size="lg" />
            </div>
          ) : references.length === 0 ? (
            <div className="bg-white rounded-lg shadow p-12 text-center">
              <h2 className="text-lg font-semibold text-gray-400 mb-2">No reference datasets found</h2>
              <p className="text-gray-400 mb-4">
                {canAdd
                  ? "Get started by adding your first reference dataset."
                  : "No reference datasets are available yet."}
              </p>
              {canAdd && (
                <button
                  onClick={() => router.push("/data/references/new")}
                  className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700"
                >
                  Add Reference
                </button>
              )}
            </div>
          ) : (
            <>
              <div className="bg-white rounded-lg shadow overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Category</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Scope</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Version</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Files</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Size</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {references.map((ref) => (
                      <tr
                        key={ref.id}
                        onClick={() => router.push(`/data/references/${ref.id}`)}
                        className="hover:bg-gray-50 cursor-pointer"
                      >
                        <td className="px-6 py-4 text-sm font-medium text-gray-900">{ref.name}</td>
                        <td className="px-6 py-4 text-sm text-gray-500 capitalize">{ref.category}</td>
                        <td className="px-6 py-4 text-sm text-gray-500 capitalize">{ref.scope}</td>
                        <td className="px-6 py-4 text-sm text-gray-500">{ref.version}</td>
                        <td className="px-6 py-4">
                          <ReferenceStatusBadge status={ref.status} />
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500">{ref.file_count ?? "—"}</td>
                        <td className="px-6 py-4 text-sm text-gray-500">{formatBytes(ref.total_size_bytes)}</td>
                        <td className="px-6 py-4 text-sm text-gray-500">
                          {new Date(ref.created_at).toLocaleDateString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="mt-4 text-sm text-gray-500">
                {total} reference dataset{total !== 1 ? "s" : ""}
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
