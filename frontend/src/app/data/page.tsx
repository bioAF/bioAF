"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";
import { ContentLoading } from "@/components/shared/ContentLoading";
import type {
  FileResponse,
  DocumentResponse,
  DocumentSearchResponse,
  StorageDashboard,
} from "@/lib/types";
import { DatasetBrowser } from "@/components/data/DatasetBrowser";

type Tab = "upload" | "datasets" | "documents" | "storage";

export default function DataPage() {
  const [activeTab, setActiveTab] = useState<Tab>("upload");

  const tabs: { key: Tab; label: string }[] = [
    { key: "upload", label: "Upload" },
    { key: "datasets", label: "Dataset Browser" },
    { key: "documents", label: "Documents" },
    { key: "storage", label: "Storage" },
  ];

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-4">Data Management</h1>

          <div className="border-b border-gray-200 mb-6">
            <nav className="flex space-x-8">
              {tabs.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`pb-3 px-1 text-sm font-medium border-b-2 ${
                    activeTab === tab.key
                      ? "border-blue-500 text-blue-600"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>

          {activeTab === "upload" && <UploadTab />}
          {activeTab === "datasets" && <DatasetBrowser />}
          {activeTab === "documents" && <DocumentsTab />}
          {activeTab === "storage" && <StorageTab />}
        </main>
      </div>
    </div>
  );
}

/* ─── Upload Tab ─── */

interface UploadState {
  files: File[];
  experimentId: string;
  uploading: boolean;
  progress: Record<string, number>;
  errors: string[];
  successes: string[];
}

function UploadTab() {
  const [state, setState] = useState<UploadState>({
    files: [],
    experimentId: "",
    uploading: false,
    progress: {},
    errors: [],
    successes: [],
  });
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const droppedFiles = Array.from(e.dataTransfer.files).filter(
      (f) =>
        f.name.endsWith(".fastq") ||
        f.name.endsWith(".fastq.gz") ||
        f.name.endsWith(".fq") ||
        f.name.endsWith(".fq.gz") ||
        f.name.endsWith(".h5ad") ||
        f.name.endsWith(".csv") ||
        f.name.endsWith(".tsv")
    );
    setState((prev) => ({ ...prev, files: [...prev.files, ...droppedFiles] }));
  }, []);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setState((prev) => ({
        ...prev,
        files: [...prev.files, ...Array.from(e.target.files!)],
      }));
    }
  };

  const removeFile = (index: number) => {
    setState((prev) => ({
      ...prev,
      files: prev.files.filter((_, i) => i !== index),
    }));
  };

  const uploadFiles = async () => {
    setState((prev) => ({ ...prev, uploading: true, errors: [], successes: [] }));

    for (const file of state.files) {
      try {
        setState((prev) => ({
          ...prev,
          progress: { ...prev.progress, [file.name]: 0 },
        }));

        // Simple upload via multipart
        const params = new URLSearchParams();
        if (state.experimentId) params.set("experiment_id", state.experimentId);
        const path = `/api/files/upload${params.toString() ? `?${params}` : ""}`;

        await api.upload<FileResponse>(path, file);

        setState((prev) => ({
          ...prev,
          progress: { ...prev.progress, [file.name]: 100 },
          successes: [...prev.successes, file.name],
        }));
      } catch (err) {
        setState((prev) => ({
          ...prev,
          errors: [...prev.errors, `${file.name}: ${err instanceof Error ? err.message : "Upload failed"}`],
        }));
      }
    }

    setState((prev) => ({ ...prev, uploading: false, files: [] }));
  };

  return (
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
          accept=".fastq,.fastq.gz,.fq,.fq.gz,.h5ad,.csv,.tsv"
          onChange={handleFileSelect}
          className="hidden"
        />
      </div>

      {/* Experiment linkage */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Link to Experiment (optional)
        </label>
        <input
          type="text"
          placeholder="Experiment ID"
          value={state.experimentId}
          onChange={(e) =>
            setState((prev) => ({ ...prev, experimentId: e.target.value }))
          }
          className="w-48 px-3 py-2 border border-gray-300 rounded-md text-sm"
        />
      </div>

      {/* File list */}
      {state.files.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-medium mb-3">
            Selected Files ({state.files.length})
          </h3>
          <ul className="space-y-2">
            {state.files.map((file, idx) => (
              <li
                key={`${file.name}-${idx}`}
                className="flex items-center justify-between text-sm"
              >
                <span className="truncate flex-1">{file.name}</span>
                <span className="text-gray-400 mx-4">
                  {(file.size / 1024 / 1024).toFixed(1)} MB
                </span>
                {state.progress[file.name] !== undefined && (
                  <div className="w-24 bg-gray-200 rounded-full h-2 mx-2">
                    <div
                      className="bg-blue-500 h-2 rounded-full transition-all"
                      style={{ width: `${state.progress[file.name]}%` }}
                    />
                  </div>
                )}
                {!state.uploading && (
                  <button
                    onClick={() => removeFile(idx)}
                    className="text-red-400 hover:text-red-600 ml-2"
                  >
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
          <button
            onClick={uploadFiles}
            disabled={state.uploading}
            className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {state.uploading ? "Uploading..." : "Upload All"}
          </button>
        </div>
      )}

      {/* Status messages */}
      {state.errors.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-md p-3">
          {state.errors.map((err, i) => (
            <p key={i} className="text-sm text-red-700">
              {err}
            </p>
          ))}
        </div>
      )}
      {state.successes.length > 0 && (
        <div className="bg-green-50 border border-green-200 rounded-md p-3">
          {state.successes.map((s, i) => (
            <p key={i} className="text-sm text-green-700">
              Uploaded: {s}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Documents Tab ─── */

function DocumentsTab() {
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchDocuments = useCallback(async () => {
    setLoading(true);
    try {
      if (searchQuery) {
        const data = await api.get<DocumentSearchResponse>(
          `/api/documents/search?query=${encodeURIComponent(searchQuery)}`
        );
        setDocuments(data.documents);
      } else {
        const data = await api.get<DocumentResponse[]>("/api/documents");
        setDocuments(data);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [searchQuery]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files?.[0]) return;
    try {
      await api.upload<DocumentResponse>("/api/documents/upload", e.target.files[0]);
      fetchDocuments();
    } catch {
      // ignore
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this document?")) return;
    try {
      await api.delete(`/api/documents/${id}`);
      fetchDocuments();
    } catch {
      // ignore
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-4">
        <input
          type="text"
          placeholder="Search document contents..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm"
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700"
        >
          Upload Document
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          onChange={handleUpload}
          className="hidden"
        />
      </div>

      {loading ? (
        <ContentLoading />
      ) : documents.length === 0 ? (
        <p className="text-gray-400 text-sm">No documents found.</p>
      ) : (
        <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="p-4 flex items-center justify-between hover:bg-gray-50"
            >
              <div>
                <p className="font-medium text-sm">{doc.title}</p>
                <p className="text-xs text-gray-400">
                  Uploaded{" "}
                  {new Date(doc.created_at).toLocaleDateString()}
                </p>
              </div>
              <div className="flex gap-2">
                <a
                  href={`/api/documents/${doc.id}/download`}
                  className="text-blue-600 text-sm hover:underline"
                >
                  Download
                </a>
                <button
                  onClick={() => handleDelete(doc.id)}
                  className="text-red-500 text-sm hover:underline"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Storage Tab ─── */

function StorageTab() {
  const [dashboard, setDashboard] = useState<StorageDashboard | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.get<StorageDashboard>("/api/storage/stats");
        setDashboard(data);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <ContentLoading />;
  if (!dashboard) return <p className="text-gray-400 text-sm">Could not load storage data.</p>;

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-sm text-gray-500">Total Storage</p>
          <p className="text-2xl font-bold">{(dashboard.total_bytes / (1024 ** 3)).toFixed(1)} GB</p>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-sm text-gray-500">Total Objects</p>
          <p className="text-2xl font-bold">
            {dashboard.buckets.reduce((sum, b) => sum + b.object_count, 0).toLocaleString()}
          </p>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <p className="text-sm text-gray-500">Est. Monthly Cost</p>
          <p className="text-2xl font-bold">${dashboard.total_cost_estimate_monthly.toFixed(2)}</p>
        </div>
      </div>

      {/* Bucket breakdown */}
      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="font-medium mb-4">Bucket Breakdown</h3>
        <div className="space-y-3">
          {dashboard.buckets.map((bucket) => {
            const bucketGb = bucket.total_bytes / (1024 ** 3);
            return (
              <div key={bucket.bucket_name}>
                <div className="flex justify-between text-sm mb-1">
                  <span>{bucket.bucket_name}</span>
                  <span className="text-gray-500">
                    {bucketGb.toFixed(2)} GB ({bucket.object_count} objects)
                  </span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-2">
                  <div
                    className="bg-blue-500 h-2 rounded-full"
                    style={{
                      width: `${
                        dashboard.total_bytes > 0
                          ? (bucket.total_bytes / dashboard.total_bytes) * 100
                          : 0
                      }%`,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Lifecycle policies */}
      {dashboard.lifecycle_policies && dashboard.lifecycle_policies.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="font-medium mb-3">Lifecycle Policies</h3>
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500">
                <th className="pb-2">Bucket</th>
                <th className="pb-2">Rule</th>
                <th className="pb-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {dashboard.lifecycle_policies.map((p, i) => (
                <tr key={i}>
                  <td className="py-1">{p.bucket_name}</td>
                  <td className="py-1">{p.rules.length} rule{p.rules.length !== 1 ? "s" : ""}</td>
                  <td className="py-1">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs ${
                        p.enabled
                          ? "bg-green-100 text-green-700"
                          : "bg-gray-100 text-gray-500"
                      }`}
                    >
                      {p.enabled ? "Active" : "Inactive"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
