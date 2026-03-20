"use client";

import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";
import type {
  FileResponse,
  FileListResponse,
  ExperimentListResponse,
} from "@/lib/types";

function formatBytes(bytes: number | null): string {
  if (bytes == null || bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function countStuckFiles(files: FileResponse[]): number {
  return files.filter(
    (f) =>
      f.experiment_id != null &&
      f.gcs_uri.includes("/uploads/")
  ).length;
}

export default function DataFilesPage() {
  const [files, setFiles] = useState<FileResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [experiments, setExperiments] = useState<
    { id: number; name: string }[]
  >([]);
  const [filterType, setFilterType] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [linkingFileIds, setLinkingFileIds] = useState<number[]>([]);
  const [selectedExperimentId, setSelectedExperimentId] = useState<string>("");
  const [reconciling, setReconciling] = useState(false);
  const [reconcileResult, setReconcileResult] = useState<{
    reconciled: number;
    failed: number;
  } | null>(null);

  const user = getCurrentUser();
  const isAdmin = user?.role === "admin";
  const stuckCount = countStuckFiles(files);

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    try {
      const params = filterType ? `?file_type=${filterType}` : "";
      const data = await api.get<FileListResponse>(`/api/files${params}`);
      setFiles(data.files);
      setSelectedIds(new Set());
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [filterType]);

  const fetchExperiments = useCallback(async () => {
    try {
      const data = await api.get<ExperimentListResponse>(
        "/api/experiments?page_size=100"
      );
      setExperiments(
        data.experiments.map((e) => ({ id: e.id, name: e.name }))
      );
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    fetchFiles();
    fetchExperiments();
  }, [fetchFiles, fetchExperiments]);

  const openLinkModal = (fileIds: number[]) => {
    setLinkingFileIds(fileIds);
    setSelectedExperimentId("");
  };

  const handleLink = async () => {
    if (linkingFileIds.length === 0 || !selectedExperimentId) return;
    try {
      await Promise.all(
        linkingFileIds.map((id) =>
          api.post(`/api/files/${id}/link`, {
            experiment_id: Number(selectedExperimentId),
          })
        )
      );
      setLinkingFileIds([]);
      setSelectedExperimentId("");
      fetchFiles();
    } catch {
      // ignore
    }
  };

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === files.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(files.map((f) => f.id)));
    }
  };

  const handleDeleteSelected = async () => {
    if (selectedIds.size === 0) return;
    const count = selectedIds.size;
    if (!confirm(`Delete ${count} ${count === 1 ? "file" : "files"}? This cannot be undone.`)) return;
    try {
      await Promise.all(
        Array.from(selectedIds).map((id) => api.delete(`/api/files/${id}`))
      );
      setSelectedIds(new Set());
      fetchFiles();
    } catch {
      // ignore
    }
  };

  const handleReconcile = async () => {
    setReconciling(true);
    setReconcileResult(null);
    try {
      const result = await api.post<{
        reconciled: number;
        failed: number;
        skipped: number;
      }>("/api/files/reconcile");
      setReconcileResult({
        reconciled: result.reconciled,
        failed: result.failed,
      });
      fetchFiles();
    } catch {
      setReconcileResult({ reconciled: 0, failed: -1 });
    } finally {
      setReconciling(false);
    }
  };

  const experimentName = (expId: number | null) => {
    if (expId == null) return null;
    return experiments.find((e) => e.id === expId)?.name ?? `#${expId}`;
  };

  const fileTypes = Array.from(new Set(files.map((f) => f.file_type))).sort();

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Files</h1>

          <div className="space-y-4">
            <div className="flex gap-4 items-center">
              <select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value)}
                className="px-3 py-2 border border-gray-300 rounded-md text-sm"
              >
                <option value="">All types</option>
                {fileTypes.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>

              {selectedIds.size > 0 && (
                <div className="flex items-center gap-3 ml-4">
                  <span className="text-sm text-gray-600">
                    {selectedIds.size} selected
                  </span>
                  <button
                    onClick={() =>
                      openLinkModal(Array.from(selectedIds))
                    }
                    className="px-3 py-1.5 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700"
                  >
                    Link to Experiment
                  </button>
                  <button
                    onClick={handleDeleteSelected}
                    className="px-3 py-1.5 bg-red-600 text-white rounded-md text-sm hover:bg-red-700"
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>

            {isAdmin && stuckCount > 0 && !reconcileResult && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-amber-800">
                    {stuckCount} {stuckCount === 1 ? "file needs" : "files need"} to be synced to storage
                  </p>
                  <p className="text-xs text-amber-600 mt-1">
                    These files are linked to an experiment but haven&apos;t been
                    moved to long-term storage yet.
                  </p>
                </div>
                <button
                  onClick={handleReconcile}
                  disabled={reconciling}
                  className="px-4 py-2 bg-amber-600 text-white rounded-md text-sm font-medium hover:bg-amber-700 disabled:opacity-50 whitespace-nowrap ml-4"
                >
                  {reconciling ? "Syncing..." : "Fix Now"}
                </button>
              </div>
            )}

            {reconcileResult && (
              <div
                className={`rounded-lg p-4 text-sm ${
                  reconcileResult.failed === -1
                    ? "bg-red-50 border border-red-200 text-red-800"
                    : reconcileResult.failed > 0
                      ? "bg-amber-50 border border-amber-200 text-amber-800"
                      : "bg-green-50 border border-green-200 text-green-800"
                }`}
              >
                {reconcileResult.failed === -1
                  ? "Something went wrong. Please try again or contact support."
                  : reconcileResult.failed > 0
                    ? `Synced ${reconcileResult.reconciled} files, but ${reconcileResult.failed} failed. Try again or contact support.`
                    : `Done! ${reconcileResult.reconciled} ${reconcileResult.reconciled === 1 ? "file" : "files"} synced to storage.`}
              </div>
            )}

            {loading ? (
              <p className="text-gray-400 text-sm">Loading...</p>
            ) : files.length === 0 ? (
              <p className="text-gray-400 text-sm">No files found.</p>
            ) : (
              <div className="bg-white rounded-lg shadow overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-3 w-10">
                        <input
                          type="checkbox"
                          checked={
                            files.length > 0 &&
                            selectedIds.size === files.length
                          }
                          onChange={toggleSelectAll}
                        />
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Filename
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Type
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Size
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Uploaded
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Uploader
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Experiment
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {files.map((file) => (
                      <tr key={file.id} className="hover:bg-gray-50">
                        <td className="px-4 py-3">
                          <input
                            type="checkbox"
                            checked={selectedIds.has(file.id)}
                            onChange={() => toggleSelect(file.id)}
                          />
                        </td>
                        <td className="px-4 py-3 text-sm font-medium">
                          {file.filename}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {file.file_type}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {formatBytes(file.size_bytes)}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {new Date(
                            file.upload_timestamp
                          ).toLocaleDateString()}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {file.uploader?.name ?? file.uploader?.email ?? "-"}
                        </td>
                        <td className="px-4 py-3 text-sm">
                          {file.experiment_id ? (
                            <span className="text-gray-700">
                              {experimentName(file.experiment_id)}
                            </span>
                          ) : (
                            <span className="flex items-center gap-2">
                              <span className="text-amber-600 text-xs font-medium">
                                Unlinked
                              </span>
                              <button
                                onClick={() => openLinkModal([file.id])}
                                className="text-blue-600 text-xs hover:underline"
                              >
                                Link
                              </button>
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {linkingFileIds.length > 0 && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md">
                <h2 className="text-lg font-semibold mb-4">
                  {linkingFileIds.length === 1
                    ? "Link to Experiment"
                    : `Link ${linkingFileIds.length} files to Experiment`}
                </h2>
                {linkingFileIds.length === 1 && (
                  <p className="text-sm text-gray-600 mb-4">
                    {files.find((f) => f.id === linkingFileIds[0])?.filename}
                  </p>
                )}
                {linkingFileIds.length > 1 && (
                  <ul className="text-sm text-gray-600 mb-4 list-disc pl-5 max-h-32 overflow-y-auto">
                    {linkingFileIds.map((id) => (
                      <li key={id}>
                        {files.find((f) => f.id === id)?.filename}
                      </li>
                    ))}
                  </ul>
                )}
                <select
                  value={selectedExperimentId}
                  onChange={(e) => setSelectedExperimentId(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm mb-4"
                >
                  <option value="">Select an experiment...</option>
                  {experiments.map((exp) => (
                    <option key={exp.id} value={String(exp.id)}>
                      {exp.name}
                    </option>
                  ))}
                </select>
                <div className="flex justify-end gap-3">
                  <button
                    onClick={() => setLinkingFileIds([])}
                    className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleLink}
                    disabled={!selectedExperimentId}
                    className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700 disabled:opacity-50"
                  >
                    Save
                  </button>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
