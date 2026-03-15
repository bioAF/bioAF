"use client";

import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";
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
                </div>
              )}
            </div>

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
