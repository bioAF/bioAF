"use client";

import { NOT_SET } from "@/lib/placeholders";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ReferenceStatusBadge } from "@/components/references/ReferenceStatusBadge";
import { StaleNotice } from "@/components/shared/StaleNotice";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useResource } from "@/hooks/useResource";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import type { ReferenceDataset, ReferenceDatasetListResponse } from "@/lib/types";

import { clickableRow } from "@/lib/a11y";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { formatBytes } from "@/lib/bytes";
import { Input, Select } from "@/components/ui/Input";
import { ListContent } from "@/components/shared/ListContent";
import { EmptyState } from "@/components/shared/EmptyState";
import { formatRecordedTime } from "@/lib/recordedTime";

export default function DataReferencesPage() {
  const router = useRouter();
  const { canAccess } = usePermissions();
  const canAdd = canAccess("references", "upload");

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

  // Keyed by the settled name search, category, scope, and status: typing waits for a pause, and
  // only the current key's answer is shown.
  const settledSearch = useDebouncedValue(search, 300);
  const params = new URLSearchParams();
  if (settledSearch) params.set("name_search", settledSearch);
  if (categoryFilter) params.set("category", categoryFilter);
  if (scopeFilter) params.set("scope", scopeFilter);
  if (statusFilter) params.set("status", statusFilter);
  const query = params.toString();
  const listPath = `/api/references${query ? `?${query}` : ""}`;
  const list = useResource(
    isAuthenticated() ? listPath : null,
    useCallback(
      (signal: AbortSignal) => api.get<ReferenceDatasetListResponse>(listPath, { signal }),
      [listPath],
    ),
    { onError: (e) => logError("loading reference data", e) },
  );
  const references: ReferenceDataset[] = list.data?.references ?? [];
  const total = list.data?.total ?? 0;
  const loading = list.loading;
  const loadError = list.error && !list.stale ? loadFailureMessage("Reference data") : null;

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

      <Card padding="sm" className="mb-6">
        <div className="flex flex-wrap gap-4">
          <Input aria-label="Search by name"
            type="text"
            width="auto"
            placeholder="Search by name..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 min-w-[200px]"
          />
          <Select aria-label="Filter by category"
            width="auto"
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
          >
            <option value="">All Categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c.charAt(0).toUpperCase() + c.slice(1)}
              </option>
            ))}
          </Select>
          <Select aria-label="Filter by scope"
            width="auto"
            value={scopeFilter}
            onChange={(e) => setScopeFilter(e.target.value)}
          >
            <option value="">All Scopes</option>
            {scopes.map((s) => (
              <option key={s} value={s}>
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </option>
            ))}
          </Select>
          <Select aria-label="Filter by status"
            width="auto"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">All Statuses</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
              </option>
            ))}
          </Select>
        </div>
      </Card>

      {list.stale && <StaleNotice what="Reference data" onRetry={() => void list.refresh()} />}

      <ListContent
        loading={loading}
        error={loadError}
        onRetry={() => void list.refresh()}
        empty={references.length === 0}
        emptyState={
          <EmptyState
            title="No reference datasets found"
            message={
              canAdd
                ? "Get started by adding your first reference dataset."
                : "No reference datasets are available yet."
            }
            action={canAdd && <Button onClick={() => router.push("/data/references/add")}>Add Reference Data</Button>}
          />
        }
      >
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
                  <td className="px-6 py-4 text-sm text-gray-500">{formatBytes(ref.total_size_bytes, "reference", { missing: NOT_SET })}</td>
                  <td className="px-6 py-4 text-sm text-gray-500">
                    {formatRecordedTime(ref.created_at, "date")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <div className="mt-4 text-sm text-gray-500">
          {total} reference dataset{total !== 1 ? "s" : ""}
        </div>
      </ListContent>
    </main>
  );
}
