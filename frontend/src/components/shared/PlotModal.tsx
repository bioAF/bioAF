"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";
import type { FileResponse } from "@/lib/types";

interface PlotMetadata {
  experimentName?: string | null;
  projectName?: string | null;
  pipelineRunId?: number | null;
  pipelineRunName?: string | null;
  notebookSessionId?: number | null;
  notebookSessionType?: string | null;
  sourceType?: string | null;
  tags?: string[];
  indexedAt?: string | null;
  file?: FileResponse | null;
}

interface PlotModalProps {
  url: string;
  title: string;
  metadata?: PlotMetadata;
  onClose: () => void;
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "-";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

function sourceLabel(sourceType: string): string {
  switch (sourceType) {
    case "pipeline":
    case "plot_archive":
      return "Pipeline";
    case "notebook":
      return "Notebook";
    case "cellxgene":
      return "CellXGene";
    case "qc_dashboard":
      return "QC Dashboard";
    case "upload":
      return "Upload";
    default:
      return sourceType;
  }
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function PlotModal({ url, title, metadata, onClose }: PlotModalProps) {
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  const file = metadata?.file;
  const hasDetail = !!metadata;

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
    } catch {
      // ignore
    }
  };

  // Simple mode for non-archive consumers (experiments, QC dashboards, results)
  if (!hasDetail) {
    return (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
        onClick={onClose}
      >
        <div
          className="relative bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-auto"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="sticky top-0 flex items-center justify-between p-4 border-b bg-white rounded-t-lg">
            <h3 className="text-lg font-semibold truncate pr-4">{title}</h3>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 text-xl leading-none px-2"
            >
              &times;
            </button>
          </div>
          <div className="p-4 flex justify-center bg-gray-50">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={url}
              alt={title}
              className="max-h-64 object-contain rounded"
            />
          </div>
        </div>
      </div>
    );
  }

  // Detail mode for plot archive -- matches Files page modal
  const isImage = file
    ? ["png", "jpg", "jpeg", "svg"].includes(file.file_type.toLowerCase())
    : true;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="relative bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-center justify-between p-4 border-b bg-white rounded-t-lg">
          <h3 className="text-lg font-semibold truncate pr-4">{title}</h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none px-2"
          >
            &times;
          </button>
        </div>

        {isImage ? (
          <div className="p-4 flex justify-center bg-gray-50">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={url}
              alt={title}
              className="max-h-64 object-contain rounded"
            />
          </div>
        ) : (
          <div className="p-4 flex justify-center bg-gray-50">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={url}
              alt={title}
              className="max-h-64 object-contain rounded"
            />
          </div>
        )}

        <dl className="p-4 grid grid-cols-2 gap-x-4 gap-y-3">
          {file && (
            <>
              <div>
                <dt className="text-xs font-medium text-gray-500 uppercase">
                  File Type
                </dt>
                <dd className="mt-0.5 text-sm text-gray-900">
                  {file.file_type}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-gray-500 uppercase">
                  Size
                </dt>
                <dd className="mt-0.5 text-sm text-gray-900">
                  {formatBytes(file.size_bytes)}
                </dd>
              </div>
            </>
          )}
          {metadata.projectName && (
            <div>
              <dt className="text-xs font-medium text-gray-500 uppercase">
                Project
              </dt>
              <dd className="mt-0.5 text-sm text-gray-900">
                {metadata.projectName}
              </dd>
            </div>
          )}
          {metadata.experimentName && (
            <div>
              <dt className="text-xs font-medium text-gray-500 uppercase">
                Experiment
              </dt>
              <dd className="mt-0.5 text-sm text-gray-900">
                {metadata.experimentName}
              </dd>
            </div>
          )}
          {metadata.pipelineRunName && (
            <div>
              <dt className="text-xs font-medium text-gray-500 uppercase">
                Pipeline
              </dt>
              <dd className="mt-0.5 text-sm text-gray-900">
                {metadata.pipelineRunName} (#{metadata.pipelineRunId})
              </dd>
            </div>
          )}
          {metadata.notebookSessionType && (
            <div>
              <dt className="text-xs font-medium text-gray-500 uppercase">
                Session
              </dt>
              <dd className="mt-0.5 text-sm text-gray-900">
                {metadata.notebookSessionType} (#{metadata.notebookSessionId})
              </dd>
            </div>
          )}
          {metadata.sourceType && (
            <div>
              <dt className="text-xs font-medium text-gray-500 uppercase">
                Source
              </dt>
              <dd className="mt-0.5 text-sm text-gray-900">
                {sourceLabel(metadata.sourceType)}
              </dd>
            </div>
          )}
          {metadata.indexedAt && (
            <div>
              <dt className="text-xs font-medium text-gray-500 uppercase">
                Indexed
              </dt>
              <dd className="mt-0.5 text-sm text-gray-900">
                {formatDate(metadata.indexedAt)}
              </dd>
            </div>
          )}
          {metadata.tags && metadata.tags.length > 0 && (
            <div className="col-span-2">
              <dt className="text-xs font-medium text-gray-500 uppercase">
                Tags
              </dt>
              <dd className="mt-0.5 flex flex-wrap gap-1">
                {metadata.tags.map((tag) => (
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
        </dl>

        {file && !file.storage_deleted && (
          <div className="px-4 pb-4">
            <button
              onClick={() => triggerDownload(file.id)}
              className="w-full px-4 py-2 bg-green-600 text-white rounded-md text-sm font-medium hover:bg-green-700 flex items-center justify-center gap-2"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-4 w-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V3"
                />
              </svg>
              Download
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
