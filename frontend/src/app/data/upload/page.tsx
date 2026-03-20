"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";
import type { ExperimentListResponse, FileResponse } from "@/lib/types";

interface ExperimentOption {
  id: number;
  name: string;
  status: string;
}

type FileStatus = "queued" | "uploading" | "complete" | "error";

interface FileItem {
  file: File;
  status: FileStatus;
  progress: number;
  error?: string;
}

export default function DataUploadPage() {
  const [items, setItems] = useState<FileItem[]>([]);
  const [experimentId, setExperimentId] = useState("");
  const [experiments, setExperiments] = useState<ExperimentOption[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .get<ExperimentListResponse>("/api/experiments?page_size=100")
      .then((data) =>
        setExperiments(
          data.experiments.map((e) => ({ id: e.id, name: e.name, status: e.status })),
        ),
      )
      .catch(() => setExperiments([]));
  }, []);

  const addFiles = (incoming: File[]) => {
    const accepted = incoming.filter(
      (f) =>
        f.name.endsWith(".fastq") ||
        f.name.endsWith(".fastq.gz") ||
        f.name.endsWith(".fq") ||
        f.name.endsWith(".fq.gz") ||
        f.name.endsWith(".h5ad") ||
        f.name.endsWith(".csv") ||
        f.name.endsWith(".tsv"),
    );
    setItems((prev) => [
      ...prev,
      ...accepted.map((f) => ({ file: f, status: "queued" as FileStatus, progress: 0 })),
    ]);
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    addFiles(Array.from(e.dataTransfer.files));
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(Array.from(e.target.files));
  };

  const removeItem = (idx: number) => {
    setItems((prev) => prev.filter((_, i) => i !== idx));
  };

  const setItemState = (idx: number, patch: Partial<FileItem>) => {
    setItems((prev) => prev.map((item, i) => (i === idx ? { ...item, ...patch } : item)));
  };

  const uploadAll = async () => {
    setUploading(true);
    const expId = experimentId ? parseInt(experimentId, 10) : undefined;

    for (let i = 0; i < items.length; i++) {
      if (items[i].status === "complete") continue;

      setItemState(i, { status: "uploading", progress: 0 });

      try {
        await api.uploadSigned<FileResponse>(items[i].file, {
          experimentId: expId,
          onProgress: (pct) => setItemState(i, { progress: pct }),
        });
        setItemState(i, { status: "complete", progress: 100 });
      } catch (err) {
        setItemState(i, {
          status: "error",
          error: err instanceof Error ? err.message : "Upload failed",
        });
      }
    }

    setUploading(false);
  };

  const pendingCount = items.filter((i) => i.status !== "complete").length;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Data Upload</h1>

          <div className="space-y-6">
            {/* Drop zone */}
            <div
              onDrop={handleDrop}
              onDragOver={(e) => e.preventDefault()}
              onClick={() => fileInputRef.current?.click()}
              className="border-2 border-dashed border-gray-300 rounded-lg p-12 text-center cursor-pointer hover:border-blue-400 transition-colors"
            >
              <p className="text-gray-500 mb-2">
                Drag & drop FASTQ, h5ad, CSV, or TSV files here
              </p>
              <p className="text-sm text-gray-400">or click to browse</p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".fastq,.fastq.gz,.fq,.fq.gz,.gz,.h5ad,.csv,.tsv"
                onChange={handleFileSelect}
                className="hidden"
              />
            </div>

            {/* Experiment linkage */}
            <div>
              <label htmlFor="experiment-select" className="block text-sm font-medium text-gray-700 mb-1">
                Link to Experiment (optional)
              </label>
              <select
                id="experiment-select"
                value={experimentId}
                onChange={(e) => setExperimentId(e.target.value)}
                className="w-80 px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
              >
                <option value="">No experiment selected</option>
                {experiments.map((exp) => (
                  <option key={exp.id} value={String(exp.id)}>
                    {exp.name}
                  </option>
                ))}
              </select>
            </div>

            {/* File list */}
            {items.length > 0 && (
              <div className="bg-white rounded-lg shadow p-4">
                <h3 className="font-medium mb-3">Files ({items.length})</h3>
                <ul className="space-y-3">
                  {items.map((item, idx) => (
                    <li key={`${item.file.name}-${idx}`} className="text-sm">
                      <div className="flex items-center justify-between mb-1">
                        <span className="truncate flex-1 mr-3">{item.file.name}</span>
                        <span className="text-gray-400 mr-3 shrink-0">
                          {(item.file.size / 1024 / 1024).toFixed(1)} MB
                        </span>
                        <StatusLabel item={item} />
                        {!uploading && item.status !== "uploading" && (
                          <button
                            onClick={() => removeItem(idx)}
                            className="text-red-400 hover:text-red-600 ml-3"
                          >
                            Remove
                          </button>
                        )}
                      </div>
                      <ProgressBar item={item} />
                      {item.status === "error" && item.error && (
                        <p className="text-xs text-red-600 mt-1">{item.error}</p>
                      )}
                    </li>
                  ))}
                </ul>

                {pendingCount > 0 && (
                  <button
                    onClick={uploadAll}
                    disabled={uploading}
                    className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700 disabled:opacity-50"
                  >
                    {uploading ? "Uploading..." : `Upload ${pendingCount} file${pendingCount !== 1 ? "s" : ""}`}
                  </button>
                )}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

function StatusLabel({ item }: { item: FileItem }) {
  if (item.status === "complete") {
    return <span className="text-xs font-medium text-green-600 shrink-0">Done</span>;
  }
  if (item.status === "error") {
    return <span className="text-xs font-medium text-red-600 shrink-0">Failed</span>;
  }
  if (item.status === "uploading") {
    return (
      <span className="text-xs font-medium text-blue-600 flex items-center gap-1 shrink-0">
        <span className="inline-block h-1.5 w-1.5 bg-blue-600 rounded-full animate-pulse" />
        {item.progress}%
      </span>
    );
  }
  return <span className="text-xs text-gray-400 shrink-0">Queued</span>;
}

function ProgressBar({ item }: { item: FileItem }) {
  if (item.status === "queued") return null;

  const barColor =
    item.status === "complete"
      ? "bg-green-500"
      : item.status === "error"
        ? "bg-red-400"
        : "bg-blue-500";

  return (
    <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
      <div
        className={`${barColor} h-1.5 rounded-full transition-all duration-300`}
        style={{ width: `${item.status === "error" ? 100 : item.progress}%` }}
      />
    </div>
  );
}
