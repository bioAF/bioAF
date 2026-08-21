"use client";

import { Modal } from "@/components/shared/Modal";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { RegistryInstallAction } from "./RegistryInstallAction";
import type {
  PipelineCatalog,
  RegistryListResponse,
  RegistryPipeline,
  RegistryRefreshResponse,
  RegistryVersionsResponse,
} from "@/lib/types";

interface Props {
  open: boolean;
  canInstall: boolean;
  onClose: () => void;
  onInstalled: () => void;
}

interface VersionPickerState {
  pipeline: RegistryPipeline;
  versions: { tag_name: string; published_at: string | null }[];
  selected: string;
  loading: boolean;
}

function formatTimeAgo(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diffMin = Math.floor((now - then) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay} d ago`;
}

function StatusChip({ p }: { p: RegistryPipeline }) {
  if (p.archived) {
    return <span className="px-2 py-0.5 text-xs rounded-full bg-gray-200 text-gray-600">Archived</span>;
  }
  if (p.update_available) {
    return (
      <span className="px-2 py-0.5 text-xs rounded-full bg-amber-100 text-amber-700">
        Update: {p.installed_version} {"->"} {p.latest_release}
      </span>
    );
  }
  if (p.installed) {
    return (
      <span className="px-2 py-0.5 text-xs rounded-full bg-green-100 text-green-700">
        Installed v{p.installed_version}
      </span>
    );
  }
  return <span className="px-2 py-0.5 text-xs rounded-full bg-blue-100 text-blue-700">Not installed</span>;
}

export function RegistryBrowseModal({ open, canInstall, onClose, onInstalled }: Props) {
  const [pipelines, setPipelines] = useState<RegistryPipeline[]>([]);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [picker, setPicker] = useState<VersionPickerState | null>(null);
  const [installing, setInstalling] = useState(false);

  async function loadPipelines() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.get<RegistryListResponse>("/api/pipelines/registry");
      setPipelines(data.pipelines);
      setLastRefreshedAt(data.last_refreshed_at);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load nf-core registry");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (open) {
      setQuery("");
      setPicker(null);
      loadPipelines();
    }
  }, [open]);

  const filtered = useMemo(() => {
    if (!query.trim()) return pipelines;
    const q = query.toLowerCase();
    return pipelines.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.description ?? "").toLowerCase().includes(q) ||
        p.topics.some((t) => t.toLowerCase().includes(q)),
    );
  }, [pipelines, query]);

  async function handleRefresh() {
    setRefreshing(true);
    setError(null);
    try {
      const data = await api.post<RegistryRefreshResponse>("/api/pipelines/registry/refresh", {});
      setLastRefreshedAt(data.last_refreshed_at);
      if (data.error) {
        setError(`Refresh failed: ${data.error}`);
      } else {
        await loadPipelines();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  async function openVersionPicker(p: RegistryPipeline) {
    setPicker({ pipeline: p, versions: [], selected: "", loading: true });
    try {
      const data = await api.get<RegistryVersionsResponse>(
        `/api/pipelines/registry/${encodeURIComponent(p.name)}/versions`,
      );
      const versions = data.versions;
      setPicker({
        pipeline: p,
        versions,
        selected: versions[0]?.tag_name ?? "",
        loading: false,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load versions");
      setPicker(null);
    }
  }

  async function confirmInstall() {
    if (!picker || !picker.selected) return;
    setInstalling(true);
    setError(null);
    try {
      await api.post<PipelineCatalog>(
        `/api/pipelines/registry/${encodeURIComponent(picker.pipeline.name)}/install`,
        { version: picker.selected },
      );
      setPicker(null);
      await loadPipelines();
      onInstalled();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Install failed");
    } finally {
      setInstalling(false);
    }
  }

  async function applyUpdate(p: RegistryPipeline) {
    if (!p.latest_release) return;
    setError(null);
    try {
      await api.patch<PipelineCatalog>(
        `/api/pipelines/version/${encodeURIComponent(`nf-core/${p.name}`)}`,
        { version: p.latest_release },
      );
      await loadPipelines();
      onInstalled();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Update failed");
    }
  }

  if (!open) return null;

  return (
    <Modal open title="Search Available Pipelines" onClose={onClose} size="xl">
    <div className="flex items-start justify-between gap-4 pb-3">
      <p className="text-xs text-gray-500">
        Browse the nf-core catalog. Registry last refreshed {formatTimeAgo(lastRefreshedAt)}.
      </p>
      {canInstall && (
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="shrink-0 text-sm px-3 py-1 rounded border border-gray-300 hover:bg-gray-50 disabled:opacity-50"
        >
          {refreshing ? "Refreshing..." : "Refresh registry"}
        </button>
      )}
    </div>

      <div className="px-6 py-3 border-b">
        <input aria-label="Search by name, description, or topic"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name, description, or topic..."
          className="w-full px-3 py-2 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
        />
      </div>

      {error && <div className="mx-6 mt-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">{error}</div>}

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading ? (
          <div className="text-center py-8 text-gray-500">Loading nf-core catalog...</div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-8 text-gray-500">No pipelines match your search</div>
        ) : (
          <ul className="divide-y divide-gray-200">
            {filtered.map((p) => (
              <li key={p.name} className="py-3 flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{p.full_name}</span>
                    {p.stars != null && (
                      <span className="text-xs text-gray-500">{p.stars.toLocaleString()} stars</span>
                    )}
                    <StatusChip p={p} />
                  </div>
                  <p className="text-sm text-gray-600 mt-0.5 line-clamp-2">{p.description || "No description"}</p>
                  {p.topics.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {p.topics.slice(0, 5).map((t) => (
                        <span key={t} className="px-1.5 py-0.5 text-xs rounded bg-gray-100 text-gray-600">
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="shrink-0 flex flex-col items-end gap-1">
                  <RegistryInstallAction
                    pipeline={p}
                    canInstall={canInstall}
                    onInstall={openVersionPicker}
                    onUpdate={applyUpdate}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {picker && (
        <div className="border-t bg-gray-50 px-6 py-4">
          <h3 className="text-sm font-medium mb-2">
            Install nf-core/{picker.pipeline.name}
          </h3>
          {picker.loading ? (
            <div className="text-sm text-gray-500">Loading versions...</div>
          ) : picker.versions.length === 0 ? (
            <div className="text-sm text-red-600">No released versions available.</div>
          ) : (
            <div className="flex items-center gap-3">
              <label htmlFor="version" className="text-sm text-gray-600">Version</label>
              <select id="version"
                value={picker.selected}
                onChange={(e) =>
                  setPicker({ ...picker, selected: e.target.value })
                }
                className="border border-gray-300 rounded px-2 py-1 text-sm"
              >
                {picker.versions.map((v) => (
                  <option key={v.tag_name} value={v.tag_name}>
                    {v.tag_name}
                    {v.published_at ? ` (${v.published_at.slice(0, 10)})` : ""}
                  </option>
                ))}
              </select>
              <button
                onClick={confirmInstall}
                disabled={installing || !picker.selected}
                className="text-sm px-3 py-1 rounded bg-bioaf-600 text-white hover:bg-bioaf-700 disabled:opacity-50"
              >
                {installing ? "Installing..." : "Install"}
              </button>
              <button
                onClick={() => setPicker(null)}
                className="text-sm px-3 py-1 text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
