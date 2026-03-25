"use client";

import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { usePermissions } from "@/hooks/usePermissions";
import { ProvenanceReportPanel } from "@/components/provenance/ProvenanceReportPanel";
import { api } from "@/lib/api";
import { getCurrentUser, getToken } from "@/lib/auth";
import type {
  FileResponse,
  FileListResponse,
  ExperimentListResponse,
} from "@/lib/types";

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "-";
  if (bytes === 0) return "0 B";
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
  const [viewingFile, setViewingFile] = useState<FileResponse | null>(null);
  const [showProvenance, setShowProvenance] = useState(false);
  const [page, setPage] = useState(1);
  const [totalFiles, setTotalFiles] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const pageSize = 25;

  const user = getCurrentUser();
  const isAdmin = user?.role_name === "admin";
  const { canAccess } = usePermissions();
  const canDownload = canAccess("files", "download");
  const stuckCount = countStuckFiles(files);

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filterType) params.set("file_type", filterType);
      params.set("page", String(page));
      params.set("page_size", String(pageSize));
      const qs = params.toString();
      const data = await api.get<FileListResponse>(`/api/files?${qs}`);
      setFiles(data.files);
      setTotalFiles(data.total);
      setSelectedIds(new Set());
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [filterType, page]);

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

  const triggerDownload = async (fileId: number) => {
    try {
      const { download_url } = await api.get<{ download_url: string }>(
        `/api/files/${fileId}/download`
      );
      const a = document.createElement("a");
      a.href = download_url;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return true;
    } catch {
      return false;
    }
  };

  const handleDownloadSelected = async () => {
    if (selectedIds.size === 0) return;
    setDownloading(true);
    setDownloadError("");
    const ids = Array.from(selectedIds);
    let completed = 0;
    const failed: string[] = [];

    for (const id of ids) {
      setDownloadProgress(`Downloading ${completed + 1} of ${ids.length} files...`);
      const ok = await triggerDownload(id);
      if (ok) {
        completed++;
      } else {
        const file = files.find((f) => f.id === id);
        failed.push(file?.filename ?? `File ${id}`);
      }
      await new Promise((r) => setTimeout(r, 200));
    }

    setDownloading(false);
    setDownloadProgress("");
    if (failed.length > 0) {
      setDownloadError(`Failed to download: ${failed.join(", ")}`);
    }
    setSelectedIds(new Set());
  };

  const experimentName = (expId: number | null) => {
    if (expId == null) return null;
    return experiments.find((e) => e.id === expId)?.name ?? `#${expId}`;
  };

  const fileTypes = Array.from(new Set(files.map((f) => f.file_type))).sort();

  const sourceLabel = (file: FileResponse): string => {
    switch (file.source_type) {
      case "upload": return file.uploader ? `Uploaded by ${file.uploader.name ?? file.uploader.email}` : "Uploaded";
      case "qc_dashboard": return `QC Dashboard${file.source_pipeline_run_id ? ` (run #${file.source_pipeline_run_id})` : ""}`;
      case "plot_archive": return `Plot Archive${file.source_pipeline_run_id ? ` (run #${file.source_pipeline_run_id})` : ""}`;
      default: return file.source_type;
    }
  };

  const isImageFile = (ft: string) => ["png", "jpg", "jpeg", "svg"].includes(ft.toLowerCase());

  const fileContentUrl = (fileId: number): string => {
    const token = getToken();
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const base = `${apiUrl}/api/files/${fileId}/content`;
    return token ? `${base}?token=${encodeURIComponent(token)}` : base;
  };

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
                onChange={(e) => { setFilterType(e.target.value); setPage(1); }}
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
                  {canDownload && (
                    <button
                      onClick={handleDownloadSelected}
                      disabled={downloading}
                      className="px-3 py-1.5 bg-green-600 text-white rounded-md text-sm hover:bg-green-700 disabled:opacity-50"
                    >
                      {downloading ? downloadProgress || "Downloading..." : "Download Selected"}
                    </button>
                  )}
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

            {downloadError && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-800 flex items-center justify-between">
                <span>{downloadError}</span>
                <button onClick={() => setDownloadError("")} className="text-red-600 hover:text-red-800 ml-4">&times;</button>
              </div>
            )}

            {loading ? (
              <ContentLoading />
            ) : files.length === 0 ? (
              <p className="text-gray-400 text-sm">No files found.</p>
            ) : (
              <div className="bg-white rounded-lg shadow overflow-x-auto">
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
                        Source
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Experiment
                      </th>
                      {canDownload && (
                        <th className="px-4 py-3 w-10">
                          <span className="sr-only">Actions</span>
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {files.map((file) => (
                      <tr
                        key={file.id}
                        className="hover:bg-gray-50 cursor-pointer"
                        onClick={() => setViewingFile(file)}
                      >
                        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={selectedIds.has(file.id)}
                            onChange={() => toggleSelect(file.id)}
                          />
                        </td>
                        <td className="px-4 py-3 text-sm font-medium text-blue-600">
                          {file.filename}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {file.file_type}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {formatBytes(file.size_bytes)}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {new Date(file.upload_timestamp).toLocaleString(undefined, {
                            month: "short",
                            day: "numeric",
                            year: "numeric",
                            hour: "numeric",
                            minute: "2-digit",
                          })}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {file.uploader?.name ?? file.uploader?.email ?? "-"}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {file.source_type === "upload" ? "Upload" : file.source_type === "qc_dashboard" ? "QC Dashboard" : file.source_type === "plot_archive" ? "Plot Archive" : file.source_type}
                        </td>
                        <td className="px-4 py-3 text-sm" onClick={(e) => e.stopPropagation()}>
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
                        {canDownload && (
                          <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                            <button
                              onClick={() => triggerDownload(file.id)}
                              title="Download"
                              className="text-gray-400 hover:text-blue-600"
                            >
                              <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3" />
                              </svg>
                            </button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>

                {/* Pagination */}
                {totalFiles > pageSize && (
                  <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 bg-gray-50">
                    <p className="text-sm text-gray-600">
                      Showing {(page - 1) * pageSize + 1}--{Math.min(page * pageSize, totalFiles)} of {totalFiles}
                    </p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setPage((p) => Math.max(1, p - 1))}
                        disabled={page <= 1}
                        className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        Previous
                      </button>
                      <span className="px-3 py-1 text-sm text-gray-600">
                        Page {page} of {Math.ceil(totalFiles / pageSize)}
                      </span>
                      <button
                        onClick={() => setPage((p) => p + 1)}
                        disabled={page * pageSize >= totalFiles}
                        className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        Next
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {viewingFile && (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
              onClick={() => setViewingFile(null)}
            >
              <div
                className="relative bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-auto"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="sticky top-0 flex items-center justify-between p-4 border-b bg-white rounded-t-lg">
                  <h3 className="text-lg font-semibold truncate pr-4">{viewingFile.filename}</h3>
                  <button
                    onClick={() => setViewingFile(null)}
                    className="text-gray-400 hover:text-gray-600 text-xl leading-none px-2"
                  >
                    &times;
                  </button>
                </div>

                {isImageFile(viewingFile.file_type) ? (
                  <div className="p-4 flex justify-center bg-gray-50">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={fileContentUrl(viewingFile.id)}
                      alt={viewingFile.filename}
                      className="max-h-64 object-contain rounded"
                    />
                  </div>
                ) : (
                  <div className="p-4 flex justify-center bg-gray-50">
                    <div className="flex flex-col items-center gap-2 py-6">
                      <div className="w-16 h-16 bg-gray-200 rounded-lg flex items-center justify-center text-gray-500 text-xs font-bold uppercase">
                        {viewingFile.file_type}
                      </div>
                      <span className="text-xs text-gray-400">No preview available</span>
                    </div>
                  </div>
                )}

                <dl className="p-4 grid grid-cols-2 gap-x-4 gap-y-3">
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">File Type</dt>
                    <dd className="mt-0.5 text-sm text-gray-900">{viewingFile.file_type}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">Size</dt>
                    <dd className="mt-0.5 text-sm text-gray-900">{formatBytes(viewingFile.size_bytes)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">Uploaded</dt>
                    <dd className="mt-0.5 text-sm text-gray-900">
                      {new Date(viewingFile.upload_timestamp).toLocaleString(undefined, {
                        month: "short", day: "numeric", year: "numeric",
                        hour: "numeric", minute: "2-digit",
                      })}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">Created</dt>
                    <dd className="mt-0.5 text-sm text-gray-900">
                      {new Date(viewingFile.created_at).toLocaleString(undefined, {
                        month: "short", day: "numeric", year: "numeric",
                        hour: "numeric", minute: "2-digit",
                      })}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">Source</dt>
                    <dd className="mt-0.5 text-sm text-gray-900">{sourceLabel(viewingFile)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">Uploader</dt>
                    <dd className="mt-0.5 text-sm text-gray-900">
                      {viewingFile.uploader?.name ?? viewingFile.uploader?.email ?? "---"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">Experiment</dt>
                    <dd className="mt-0.5 text-sm text-gray-900">
                      {viewingFile.experiment_id ? experimentName(viewingFile.experiment_id) : "---"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">MD5</dt>
                    <dd className="mt-0.5 text-sm text-gray-900 font-mono text-xs break-all">
                      {viewingFile.md5_checksum ?? "---"}
                    </dd>
                  </div>
                  {viewingFile.tags.length > 0 && (
                    <div className="col-span-2">
                      <dt className="text-xs font-medium text-gray-500 uppercase">Tags</dt>
                      <dd className="mt-0.5 flex flex-wrap gap-1">
                        {viewingFile.tags.map((tag) => (
                          <span
                            key={tag}
                            className="px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded"
                          >
                            {tag}
                          </span>
                        ))}
                      </dd>
                    </div>
                  )}
                  <div className="col-span-2">
                    <dt className="text-xs font-medium text-gray-500 uppercase">GCS URI</dt>
                    <dd className="mt-0.5 text-xs text-gray-600 font-mono break-all">
                      {viewingFile.gcs_uri}
                    </dd>
                  </div>
                </dl>

                {canDownload && (
                  <div className="px-4 pb-4 flex gap-3">
                    <button
                      onClick={() => triggerDownload(viewingFile.id)}
                      className="flex-1 px-4 py-2 bg-green-600 text-white rounded-md text-sm font-medium hover:bg-green-700 flex items-center justify-center gap-2"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3" />
                      </svg>
                      Download
                    </button>
                    <button
                      onClick={() => setShowProvenance(true)}
                      className="px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-sm font-medium hover:bg-gray-50"
                    >
                      View Provenance
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {showProvenance && viewingFile && (
            <div
              className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40"
              onClick={() => setShowProvenance(false)}
            >
              <div
                className="relative bg-white rounded-lg shadow-xl w-full max-w-2xl mx-4 max-h-[80vh] overflow-auto"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="sticky top-0 flex items-center justify-between p-4 border-b bg-white rounded-t-lg">
                  <h3 className="text-lg font-semibold">Provenance: {viewingFile.filename}</h3>
                  <button
                    onClick={() => setShowProvenance(false)}
                    className="text-gray-400 hover:text-gray-600 text-xl leading-none px-2"
                  >
                    &times;
                  </button>
                </div>
                <div className="p-4">
                  <ProvenanceReportPanel
                    entityType="artifact"
                    entityId={viewingFile.id}
                    entityName={viewingFile.filename}
                  />
                </div>
              </div>
            </div>
          )}

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
