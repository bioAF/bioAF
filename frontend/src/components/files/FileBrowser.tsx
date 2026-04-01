"use client";

import { useState, useEffect, useCallback } from "react";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { ProvenanceReportPanel } from "@/components/provenance/ProvenanceReportPanel";
import { usePermissions } from "@/hooks/usePermissions";
import { api, fileContentUrl } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";
import type {
  FileResponse,
  FileListResponse,
  ExperimentListResponse,
  ProjectListResponse,
  SampleBrief,
} from "@/lib/types";

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "-";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function isImageFile(ft: string) {
  return ["png", "jpg", "jpeg", "svg"].includes(ft.toLowerCase());
}

interface Props {
  experimentId?: number;
  projectId?: number;
}

export function FileBrowser({ experimentId, projectId }: Props) {
  const [files, setFiles] = useState<FileResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);
  const [experiments, setExperiments] = useState<{ id: number; name: string }[]>([]);
  const [filterType, setFilterType] = useState("");
  const [filterSource, setFilterSource] = useState("");
  const [filterSampleId, setFilterSampleId] = useState("");
  const [samples, setSamples] = useState<{ id: number; label: string }[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [viewingFile, setViewingFile] = useState<FileResponse | null>(null);
  const [showProvenance, setShowProvenance] = useState(false);
  const [page, setPage] = useState(1);
  const [totalFiles, setTotalFiles] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const pageSize = 25;

  // Link modal state
  const [linkingFileIds, setLinkingFileIds] = useState<number[]>([]);
  const [linkProjectId, setLinkProjectId] = useState("");
  const [linkExperimentId, setLinkExperimentId] = useState("");
  const [linkExperiments, setLinkExperiments] = useState<{ id: number; name: string }[]>([]);
  const [linkSampleId, setLinkSampleId] = useState("");
  const [linkSamples, setLinkSamples] = useState<{ id: number; label: string }[]>([]);

  const user = getCurrentUser();
  const isAdmin = user?.role_name === "admin";
  const { canAccess } = usePermissions();
  const canDownload = canAccess("files", "download");

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filterType) params.set("file_type", filterType);
      if (filterSource) params.set("source_type", filterSource);
      if (filterSampleId) params.set("sample_id", filterSampleId);
      if (experimentId != null) params.set("experiment_id", String(experimentId));
      if (projectId != null) params.set("project_id", String(projectId));
      params.set("page", String(page));
      params.set("page_size", String(pageSize));
      const data = await api.get<FileListResponse>(`/api/files?${params.toString()}`);
      setFiles(data.files);
      setTotalFiles(data.total);
      setSelectedIds(new Set());
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [experimentId, projectId, filterType, filterSource, filterSampleId, page]);

  const fetchMeta = useCallback(async () => {
    try {
      const [projData, expData] = await Promise.all([
        api.get<ProjectListResponse>("/api/projects?page_size=100"),
        api.get<ExperimentListResponse>("/api/experiments?page_size=100"),
      ]);
      setProjects(projData.projects.map((p) => ({ id: p.id, name: p.name })));
      setExperiments(expData.experiments.map((e) => ({ id: e.id, name: e.name })));
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    fetchFiles();
    fetchMeta();
  }, [fetchFiles, fetchMeta]);

  // Fetch samples when viewing files for an experiment
  useEffect(() => {
    if (!experimentId) {
      setSamples([]);
      return;
    }
    api
      .get<SampleBrief[]>(`/api/experiments/${experimentId}/samples`)
      .then((data) =>
        setSamples(
          data.map((s) => ({
            id: s.id,
            label: s.sample_id_external ?? `Sample #${s.id}`,
          })),
        ),
      )
      .catch(() => setSamples([]));
  }, [experimentId]);

  // Reload link experiments when link project changes
  useEffect(() => {
    setLinkExperimentId("");
    setLinkSampleId("");
    setLinkSamples([]);
    const qs = linkProjectId
      ? `?project_id=${linkProjectId}&page_size=100`
      : "?page_size=100";
    api
      .get<ExperimentListResponse>(`/api/experiments${qs}`)
      .then((data) =>
        setLinkExperiments(data.experiments.map((e) => ({ id: e.id, name: e.name }))),
      )
      .catch(() => setLinkExperiments([]));
  }, [linkProjectId]);

  // Load samples when link experiment changes
  useEffect(() => {
    setLinkSampleId("");
    if (!linkExperimentId) {
      setLinkSamples([]);
      return;
    }
    api
      .get<SampleBrief[]>(`/api/experiments/${linkExperimentId}/samples`)
      .then((data) =>
        setLinkSamples(
          data.map((s) => ({
            id: s.id,
            label: s.sample_id_external ?? `Sample #${s.id}`,
          })),
        ),
      )
      .catch(() => setLinkSamples([]));
  }, [linkExperimentId]);

  const openLinkModal = (fileIds: number[]) => {
    setLinkingFileIds(fileIds);
    setLinkProjectId("");
    setLinkExperimentId("");
    setLinkSampleId("");
    setLinkSamples([]);
  };

  const handleLink = async () => {
    if (linkingFileIds.length === 0) return;
    if (!linkProjectId && !linkExperimentId && !linkSampleId) return;

    const body: Record<string, number> = {};
    if (linkProjectId) body.project_id = Number(linkProjectId);
    if (linkExperimentId) body.experiment_id = Number(linkExperimentId);
    if (linkSampleId) body.sample_id = Number(linkSampleId);

    try {
      await Promise.all(
        linkingFileIds.map((id) => api.post(`/api/files/${id}/link`, body)),
      );
      setLinkingFileIds([]);
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
        Array.from(selectedIds).map((id) => api.delete(`/api/files/${id}`)),
      );
      setSelectedIds(new Set());
      fetchFiles();
    } catch {
      // ignore
    }
  };

  const triggerDownload = async (fileId: number) => {
    try {
      const { download_url } = await api.get<{ download_url: string }>(
        `/api/files/${fileId}/download`,
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

  const projectName = (projId: number | null) => {
    if (projId == null) return null;
    return projects.find((p) => p.id === projId)?.name ?? `#${projId}`;
  };

  const sourceLabel = (file: FileResponse): string => {
    switch (file.source_type) {
      case "upload":
        return file.uploader
          ? `Uploaded by ${file.uploader.name ?? file.uploader.email}`
          : "Uploaded";
      case "pipeline_output":
        return `Nextflow${file.source_pipeline_run_id ? ` (run #${file.source_pipeline_run_id})` : ""}`;
      case "notebook_output":
        return `RStudio${file.source_notebook_session_id ? ` (session #${file.source_notebook_session_id})` : ""}`;
      case "qc_dashboard":
        return `QC Dashboard${file.source_pipeline_run_id ? ` (run #${file.source_pipeline_run_id})` : ""}`;
      case "plot_archive":
        return `Plot Archive${file.source_pipeline_run_id ? ` (run #${file.source_pipeline_run_id})` : ""}`;
      default:
        return file.source_type;
    }
  };

  const associationLabel = (file: FileResponse) => {
    if (file.experiment_id != null) return experimentName(file.experiment_id);
    if (file.project_id != null) return projectName(file.project_id);
    return null;
  };

  const associationBadge = (file: FileResponse) => {
    if (file.experiment_id != null) return null;
    if (file.project_id != null) return "project";
    return null;
  };

  const sampleLabel = (file: FileResponse) => {
    if (!file.sample_ids || file.sample_ids.length === 0) return null;
    return file.sample_ids.length === 1 ? `1 sample` : `${file.sample_ids.length} samples`;
  };

  const fileTypes = Array.from(new Set(files.map((f) => f.file_type))).sort();
  const sourceTypes: { value: string; label: string }[] = [
    { value: "upload", label: "Upload" },
    { value: "pipeline_output", label: "Nextflow" },
    { value: "notebook_output", label: "RStudio" },
    { value: "qc_dashboard", label: "QC Dashboard" },
    { value: "plot_archive", label: "Plot Archive" },
  ];
  const linkModalHasSelection = !!(linkProjectId || linkExperimentId || linkSampleId);

  return (
    <div className="space-y-4">
      <div className="flex gap-4 items-center flex-wrap">
        {samples.length > 0 && (
          <select
            value={filterSampleId}
            onChange={(e) => {
              setFilterSampleId(e.target.value);
              setPage(1);
            }}
            className="px-3 py-2 border border-gray-300 rounded-md text-sm"
          >
            <option value="">All samples</option>
            {samples.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.label}
              </option>
            ))}
          </select>
        )}

        <select
          value={filterType}
          onChange={(e) => {
            setFilterType(e.target.value);
            setPage(1);
          }}
          className="px-3 py-2 border border-gray-300 rounded-md text-sm"
        >
          <option value="">All types</option>
          {fileTypes.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        <select
          value={filterSource}
          onChange={(e) => {
            setFilterSource(e.target.value);
            setPage(1);
          }}
          className="px-3 py-2 border border-gray-300 rounded-md text-sm"
        >
          <option value="">All sources</option>
          {sourceTypes.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>

        {selectedIds.size > 0 && (
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-600">{selectedIds.size} selected</span>
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
              onClick={() => openLinkModal(Array.from(selectedIds))}
              className="px-3 py-1.5 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700"
            >
              Associate
            </button>
            {isAdmin && (
              <button
                onClick={handleDeleteSelected}
                className="px-3 py-1.5 bg-red-600 text-white rounded-md text-sm hover:bg-red-700"
              >
                Delete
              </button>
            )}
          </div>
        )}
      </div>

      {downloadError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-800 flex items-center justify-between">
          <span>{downloadError}</span>
          <button onClick={() => setDownloadError("")} className="text-red-600 hover:text-red-800 ml-4">
            &times;
          </button>
        </div>
      )}

      {loading ? (
        <ContentLoading />
      ) : files.length === 0 ? (
        <p className="text-gray-400 text-sm py-8 text-center">No files found.</p>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 w-10">
                  <input
                    type="checkbox"
                    checked={files.length > 0 && selectedIds.size === files.length}
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
                  Association
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
                  <td className="px-4 py-3 text-sm font-medium flex items-center gap-1.5">
                    {file.storage_deleted && (
                      <span title="Storage deleted" className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-red-100 text-red-600 flex-shrink-0">
                        <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor">
                          <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                        </svg>
                      </span>
                    )}
                    <span className={file.storage_deleted ? "text-gray-400" : "text-blue-600"}>
                      {file.filename}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">{file.file_type}</td>
                  <td className="px-4 py-3 text-sm text-gray-500">{formatBytes(file.size_bytes)}</td>
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
                    {sourceLabel(file)}
                  </td>
                  <td className="px-4 py-3 text-sm" onClick={(e) => e.stopPropagation()}>
                    {associationLabel(file) ? (
                      <span className="text-gray-700 flex flex-col gap-0.5">
                        <span className="flex items-center gap-1.5">
                          {associationBadge(file) === "project" && (
                            <span className="text-xs text-purple-600 font-medium bg-purple-50 px-1.5 py-0.5 rounded">
                              Project
                            </span>
                          )}
                          {associationLabel(file)}
                        </span>
                        {sampleLabel(file) && (
                          <span className="text-xs text-teal-700 font-medium bg-teal-50 px-1.5 py-0.5 rounded w-fit">
                            {sampleLabel(file)}
                          </span>
                        )}
                      </span>
                    ) : (
                      <span className="flex items-center gap-2">
                        <span className="text-amber-600 text-xs font-medium">Unlinked</span>
                        <button
                          onClick={() => openLinkModal([file.id])}
                          className="text-blue-600 text-xs hover:underline"
                        >
                          Associate
                        </button>
                      </span>
                    )}
                  </td>
                  {canDownload && (
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      {!file.storage_deleted && (
                        <button
                          onClick={() => triggerDownload(file.id)}
                          title="Download"
                          className="text-gray-400 hover:text-blue-600"
                        >
                          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3" />
                          </svg>
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>

          {totalFiles > pageSize && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 bg-gray-50">
              <p className="text-sm text-gray-600">
                Showing {(page - 1) * pageSize + 1}--{Math.min(page * pageSize, totalFiles)} of{" "}
                {totalFiles}
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

      {/* File detail modal */}
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
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-gray-500 uppercase">Created</dt>
                <dd className="mt-0.5 text-sm text-gray-900">
                  {new Date(viewingFile.created_at).toLocaleString(undefined, {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
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
                <dt className="text-xs font-medium text-gray-500 uppercase">Project</dt>
                <dd className="mt-0.5 text-sm text-gray-900">
                  {viewingFile.project_id ? projectName(viewingFile.project_id) : "---"}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-gray-500 uppercase">Experiment</dt>
                <dd className="mt-0.5 text-sm text-gray-900">
                  {viewingFile.experiment_id ? experimentName(viewingFile.experiment_id) : "---"}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-gray-500 uppercase">Sample</dt>
                <dd className="mt-0.5 text-sm text-gray-900">
                  {viewingFile.sample_ids && viewingFile.sample_ids.length > 0
                    ? viewingFile.sample_ids.map((id) => (
                        <span
                          key={id}
                          className="inline-block mr-1 px-1.5 py-0.5 bg-teal-50 text-teal-700 text-xs rounded"
                        >
                          #{id}
                        </span>
                      ))
                    : "---"}
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

            <div className="px-4 pb-4 flex gap-3">
              {canDownload && !viewingFile.storage_deleted && (
                <button
                  onClick={() => triggerDownload(viewingFile.id)}
                  className="flex-1 px-4 py-2 bg-green-600 text-white rounded-md text-sm font-medium hover:bg-green-700 flex items-center justify-center gap-2"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3" />
                  </svg>
                  Download
                </button>
              )}
              {viewingFile.storage_deleted && (
                <div className="flex-1 px-4 py-2 bg-red-50 text-red-600 rounded-md text-sm font-medium flex items-center justify-center gap-2">
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                  </svg>
                  Storage deleted
                </div>
              )}
              <button
                onClick={() => setShowProvenance(true)}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-sm font-medium hover:bg-gray-50"
              >
                View Provenance
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Provenance modal */}
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

      {/* Associate modal */}
      {linkingFileIds.length > 0 && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md">
            <h2 className="text-lg font-semibold mb-1">
              {linkingFileIds.length === 1
                ? "Associate File"
                : `Associate ${linkingFileIds.length} Files`}
            </h2>
            <p className="text-xs text-gray-500 mb-4">
              Link to a project, experiment, or specific sample. Select the most specific level that
              applies.
            </p>
            {linkingFileIds.length === 1 && (
              <p className="text-sm text-gray-600 mb-4 font-medium">
                {files.find((f) => f.id === linkingFileIds[0])?.filename}
              </p>
            )}
            {linkingFileIds.length > 1 && (
              <ul className="text-sm text-gray-600 mb-4 list-disc pl-5 max-h-24 overflow-y-auto">
                {linkingFileIds.map((id) => (
                  <li key={id}>{files.find((f) => f.id === id)?.filename}</li>
                ))}
              </ul>
            )}

            <div className="space-y-3">
              <div>
                <label
                  htmlFor="fb-link-project"
                  className="block text-xs font-medium text-gray-600 mb-1"
                >
                  Project
                </label>
                <select
                  id="fb-link-project"
                  value={linkProjectId}
                  onChange={(e) => setLinkProjectId(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
                >
                  <option value="">No project</option>
                  {projects.map((p) => (
                    <option key={p.id} value={String(p.id)}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  htmlFor="fb-link-experiment"
                  className="block text-xs font-medium text-gray-600 mb-1"
                >
                  Experiment
                </label>
                <select
                  id="fb-link-experiment"
                  value={linkExperimentId}
                  onChange={(e) => setLinkExperimentId(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
                >
                  <option value="">No experiment</option>
                  {linkExperiments.map((exp) => (
                    <option key={exp.id} value={String(exp.id)}>
                      {exp.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  htmlFor="fb-link-sample"
                  className="block text-xs font-medium text-gray-600 mb-1"
                >
                  Sample
                </label>
                <select
                  id="fb-link-sample"
                  value={linkSampleId}
                  onChange={(e) => setLinkSampleId(e.target.value)}
                  disabled={!linkExperimentId}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white disabled:bg-gray-50 disabled:text-gray-400"
                >
                  <option value="">No sample</option>
                  {linkSamples.map((s) => (
                    <option key={s.id} value={String(s.id)}>
                      {s.label}
                    </option>
                  ))}
                </select>
                {!linkExperimentId && (
                  <p className="text-xs text-gray-400 mt-1">Select an experiment first</p>
                )}
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-5">
              <button
                onClick={() => setLinkingFileIds([])}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
              >
                Cancel
              </button>
              <button
                onClick={handleLink}
                disabled={!linkModalHasSelection}
                className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700 disabled:opacity-50"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
