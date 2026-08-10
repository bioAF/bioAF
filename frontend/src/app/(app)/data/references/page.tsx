"use client";

import { NOT_SET } from "@/lib/placeholders";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ReferenceStatusBadge } from "@/components/references/ReferenceStatusBadge";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorState } from "@/components/shared/ErrorState";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import type { ReferenceDataset, ReferenceDatasetListResponse } from "@/lib/types";

import { clickableRow } from "@/lib/a11y";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

function formatBytes(bytes: number | null): string {
  if (bytes == null) return NOT_SET;
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
  const [loadError, setLoadError] = useState<string | null>(null);
  // Bumped by Retry: the load lives in an effect keyed on the filters, so this
  // is what re-triggers it without changing what the user asked for.
  const [reloadKey, setReloadKey] = useState(0);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [scopeFilter, setScopeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  // Served by the API rather than hard-coded here. The previous local lists never
  // matched the model: scopes were ["global", "organization"] against
  // REFERENCE_SCOPES of ["public", "internal"], so every scope filter returned
  // zero rows, and the category list invented "transcriptome" while omitting
  // "atlas" and "markers", which the upload form can create.
  const [categories, setCategories] = useState<string[]>([]);
  const [scopes, setScopes] = useState<string[]>([]);
  const statuses = ["active", "deprecated", "pending_approval"];

  useEffect(() => {
    if (!isAuthenticated()) return;
    api
      .get<{ categories: string[]; scopes: string[] }>("/api/references/filter-options")
      .then((data) => {
        setCategories(data.categories);
        setScopes(data.scopes);
      })
      .catch(() => {
        // Leave the filters empty rather than offering values the API will reject.
      });
  }, []);

  useEffect(() => {
    if (!isAuthenticated()) return;
    setLoading(true);

    const params = new URLSearchParams();
    if (search) params.set("name_search", search);
    if (categoryFilter) params.set("category", categoryFilter);
    if (scopeFilter) params.set("scope", scopeFilter);
    if (statusFilter) params.set("status", statusFilter);

    const query = params.toString();
    setLoadError(null);
    api
      .get<ReferenceDatasetListResponse>(`/api/references${query ? `?${query}` : ""}`)
      .then((data) => {
        setReferences(data.references);
        setTotal(data.total);
      })
      .catch((e) => {
        logError("loading reference data", e);
        setLoadError(loadFailureMessage("Reference data"));
      })
      .finally(() => setLoading(false));
  }, [search, categoryFilter, scopeFilter, statusFilter, reloadKey]);

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Reference Data</h1>
          <p data-testid="page-description" className="text-sm text-gray-500 mt-1">
            Genomes, annotations and index files that pipelines read at run time.
          </p>
        </div>
        {canAdd && (
          <Button
            onClick={() => router.push("/data/references/add")}>
            Add Reference Data
          </Button>
        )}
      </div>

      <div className="bg-white rounded-lg shadow mb-6 p-4">
        <div className="flex flex-wrap gap-4">
          <input aria-label="Search by name"
            type="text"
            placeholder="Search by name..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="border border-gray-300 rounded-md px-3 py-2 text-sm flex-1 min-w-[200px]"
          />
          <select aria-label="Filter by category"
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
          <select aria-label="Filter by scope"
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
          <select aria-label="Filter by status"
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
      ) : loadError ? (
        <Card padding="none">
          <ErrorState
            message={loadError}
            onRetry={() => setReloadKey((k) => k + 1)}
          />
        </Card>
      ) : references.length === 0 ? (
        <Card padding="none" className="p-12 text-center">
          <h2 className="text-lg font-semibold text-gray-500 mb-2">No reference datasets found</h2>
          <p className="text-gray-500 mb-4">
            {canAdd
              ? "Get started by adding your first reference dataset."
              : "No reference datasets are available yet."}
          </p>
          {canAdd && (
            <Button
              onClick={() => router.push("/data/references/add")}>
              Add Reference Data
            </Button>
          )}
        </Card>
      ) : (
        <>
          <Card padding="none" className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Category</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Scope</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Version</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Files</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Size</th>
                  <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {references.map((ref) => (
                  <tr
                    key={ref.id}
                    {...clickableRow(() => router.push(`/data/references/${ref.id}`))}
                    className="hover:bg-gray-50 cursor-pointer"
                  >
                    <td className="px-6 py-4 text-sm font-medium text-gray-900">{ref.name}</td>
                    <td className="px-6 py-4 text-sm text-gray-500 capitalize">{ref.category}</td>
                    <td className="px-6 py-4 text-sm text-gray-500 capitalize">{ref.scope}</td>
                    <td className="px-6 py-4 text-sm text-gray-500">{ref.version}</td>
                    <td className="px-6 py-4">
                      <ReferenceStatusBadge status={ref.status} />
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">{ref.file_count ?? NOT_SET}</td>
                    <td className="px-6 py-4 text-sm text-gray-500">{formatBytes(ref.total_size_bytes)}</td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {new Date(ref.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <div className="mt-4 text-sm text-gray-500">
            {total} reference dataset{total !== 1 ? "s" : ""}
          </div>
        </>
      )}
    </main>
  );
}
