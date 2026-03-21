"use client";

import { useState, useEffect, useCallback } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";
import type {
  CellxgenePublicationResponse,
  FileResponse,
  ExperimentListResponse,
  Experiment,
} from "@/lib/types";

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "Unknown size";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function StatusBadge({ status }: { status: string }) {
  const colorClass = (() => {
    switch (status) {
      case "published":
      case "running":
        return "bg-green-100 text-green-700";
      case "publishing":
        return "bg-blue-100 text-blue-700";
      case "unpublished":
        return "bg-gray-100 text-gray-500";
      case "failed":
        return "bg-red-100 text-red-700";
      default:
        return "bg-yellow-100 text-yellow-700";
    }
  })();

  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colorClass}`}>
      {status}
    </span>
  );
}

function PublishForm({
  onPublish,
  onCancel,
}: {
  onPublish: (fileId: number, datasetName: string, experimentId: number | null) => void;
  onCancel: () => void;
}) {
  const [files, setFiles] = useState<FileResponse[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(true);
  const [selectedFileId, setSelectedFileId] = useState<number | null>(null);
  const [datasetName, setDatasetName] = useState("");
  const [experimentId, setExperimentId] = useState<number | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.get<FileResponse[]>("/api/cellxgene/publishable-files");
        setFiles(data);
      } catch {
        // ignore
      } finally {
        setLoadingFiles(false);
      }
    })();
  }, []);

  const handleFileSelect = (fileId: number) => {
    setSelectedFileId(fileId);
    const file = files.find((f) => f.id === fileId);
    if (file) {
      // Auto-populate dataset name from filename (strip .h5ad extension)
      const suggestedName = file.filename.replace(/\.h5ad$/i, "");
      setDatasetName(suggestedName);
      setExperimentId(file.experiment_id);
    }
  };

  const selectedFile = files.find((f) => f.id === selectedFileId);
  const canPublish = selectedFileId != null && datasetName.trim().length > 0;

  return (
    <div className="bg-white rounded-lg shadow p-5 space-y-4 mb-6">
      <h3 className="font-semibold text-sm">Publish Dataset to cellxgene</h3>

      {/* File picker */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Select h5ad file</label>
        {loadingFiles ? (
          <p className="text-sm text-gray-400">Loading available files...</p>
        ) : files.length === 0 ? (
          <p className="text-sm text-gray-400">No publishable h5ad files available.</p>
        ) : (
          <select
            value={selectedFileId ?? ""}
            onChange={(e) => handleFileSelect(parseInt(e.target.value))}
            className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
          >
            <option value="" disabled>Choose a file...</option>
            {files.map((f) => (
              <option key={f.id} value={f.id}>
                {f.filename} ({formatBytes(f.size_bytes)})
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Selected file details */}
      {selectedFile && (
        <div className="bg-gray-50 rounded p-3 text-sm text-gray-600 space-y-1">
          <p><span className="font-medium">File:</span> {selectedFile.filename}</p>
          <p><span className="font-medium">Size:</span> {formatBytes(selectedFile.size_bytes)}</p>
          {selectedFile.experiment_id && (
            <p><span className="font-medium">Experiment ID:</span> {selectedFile.experiment_id}</p>
          )}
          <p><span className="font-medium">Uploaded:</span> {new Date(selectedFile.upload_timestamp).toLocaleDateString()}</p>
        </div>
      )}

      {/* Dataset name */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Dataset name</label>
        <input
          type="text"
          value={datasetName}
          onChange={(e) => setDatasetName(e.target.value)}
          placeholder="Name for this cellxgene dataset"
          className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
        />
      </div>

      {/* Actions */}
      <div className="flex gap-3">
        <button
          onClick={() => {
            if (selectedFileId && canPublish) {
              onPublish(selectedFileId, datasetName.trim(), experimentId);
            }
          }}
          disabled={!canPublish}
          className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          Publish
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2 text-gray-600 bg-gray-100 rounded-md text-sm hover:bg-gray-200"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function PublicationCard({
  pub,
  experiments,
  onUnpublish,
}: {
  pub: CellxgenePublicationResponse;
  experiments: Map<number, string>;
  onUnpublish: (id: number) => void;
}) {
  const experimentName = pub.experiment_id ? experiments.get(pub.experiment_id) : null;
  const isActive = pub.status === "published" || pub.status === "running";

  return (
    <div className="p-4 flex items-start justify-between hover:bg-gray-50">
      <div className="space-y-1 min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="font-medium text-sm truncate">{pub.dataset_name}</p>
          <StatusBadge status={pub.status} />
        </div>
        <div className="text-xs text-gray-400 space-y-0.5">
          {experimentName && <p>Experiment: {experimentName}</p>}
          {pub.file && (
            <p>{pub.file.filename} ({formatBytes(pub.file.size_bytes)})</p>
          )}
          <p>
            {pub.published_by && `Published by ${pub.published_by.name}`}
            {pub.published_at && ` on ${new Date(pub.published_at).toLocaleDateString()}`}
          </p>
        </div>
      </div>
      <div className="flex gap-3 items-center ml-4 shrink-0">
        {isActive && pub.stable_url && (
          <a
            href={pub.stable_url}
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1 text-sm font-medium text-blue-600 bg-blue-50 rounded hover:bg-blue-100"
          >
            Open
          </a>
        )}
        {isActive && (
          <button
            onClick={() => onUnpublish(pub.id)}
            className="text-red-500 text-sm hover:underline"
          >
            Unpublish
          </button>
        )}
      </div>
    </div>
  );
}

export default function CellxgenePage() {
  const [publications, setPublications] = useState<CellxgenePublicationResponse[]>([]);
  const [experiments, setExperiments] = useState<Map<number, string>>(new Map());
  const [loading, setLoading] = useState(true);
  const [showPublishForm, setShowPublishForm] = useState(false);
  const [filterExperimentId, setFilterExperimentId] = useState<number | null>(null);

  const fetchPublications = useCallback(async () => {
    setLoading(true);
    try {
      const url = filterExperimentId
        ? `/api/cellxgene?experiment_id=${filterExperimentId}`
        : "/api/cellxgene";
      const data = await api.get<CellxgenePublicationResponse[]>(url);
      setPublications(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [filterExperimentId]);

  useEffect(() => {
    fetchPublications();
  }, [fetchPublications]);

  // Load experiment names for display
  useEffect(() => {
    (async () => {
      try {
        const data = await api.get<ExperimentListResponse>("/api/experiments?page_size=100");
        const map = new Map<number, string>();
        for (const exp of data.experiments) {
          map.set(exp.id, exp.name);
        }
        setExperiments(map);
      } catch {
        // ignore
      }
    })();
  }, []);

  const handlePublish = async (fileId: number, datasetName: string, experimentId: number | null) => {
    try {
      await api.post("/api/cellxgene/publish", {
        file_id: fileId,
        experiment_id: experimentId,
        dataset_name: datasetName,
      });
      setShowPublishForm(false);
      fetchPublications();
    } catch {
      // ignore
    }
  };

  const handleUnpublish = async (id: number) => {
    if (!confirm("Unpublish this dataset? The cellxgene viewer will be shut down.")) return;
    try {
      await api.delete(`/api/cellxgene/${id}`);
      fetchPublications();
    } catch {
      // ignore
    }
  };

  // Unique experiment IDs from publications for filter dropdown
  const pubExperimentIds = [...new Set(publications.map((p) => p.experiment_id).filter((id): id is number => id != null))];

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-2">
            <h1 className="text-2xl font-bold">cellxgene Explorer</h1>
            <button
              onClick={() => setShowPublishForm(!showPublishForm)}
              className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700"
            >
              Publish Dataset
            </button>
          </div>

          <p className="text-sm text-gray-500 mb-6">
            Publish h5ad datasets for interactive single-cell exploration with cellxgene.
          </p>

          {showPublishForm && (
            <PublishForm
              onPublish={handlePublish}
              onCancel={() => setShowPublishForm(false)}
            />
          )}

          {/* Experiment filter */}
          {pubExperimentIds.length > 0 && (
            <div className="mb-4">
              <select
                value={filterExperimentId ?? ""}
                onChange={(e) => setFilterExperimentId(e.target.value ? parseInt(e.target.value) : null)}
                className="px-3 py-1.5 border border-gray-300 rounded-md text-sm bg-white"
              >
                <option value="">All experiments</option>
                {pubExperimentIds.map((id) => (
                  <option key={id} value={id}>
                    {experiments.get(id) || `Experiment #${id}`}
                  </option>
                ))}
              </select>
            </div>
          )}

          {loading ? (
            <p className="text-gray-400 text-sm">Loading...</p>
          ) : publications.length === 0 ? (
            <p className="text-gray-400 text-sm">
              {filterExperimentId
                ? "No published datasets for this experiment."
                : "No published datasets yet. Use the Publish Dataset button to get started."}
            </p>
          ) : (
            <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
              {publications.map((pub) => (
                <PublicationCard
                  key={pub.id}
                  pub={pub}
                  experiments={experiments}
                  onUnpublish={handleUnpublish}
                />
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
