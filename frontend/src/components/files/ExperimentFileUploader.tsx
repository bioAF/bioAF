"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useCapabilities } from "@/hooks/useCapabilities";
import { suggestFilename, splitExtension, todayDateStr } from "@/lib/fileNaming";
import type { ExperimentDetail, FileResponse, Project } from "@/lib/types";

type FileStatus = "queued" | "uploading" | "complete" | "error";

interface FileItem {
  file: File;
  status: FileStatus;
  progress: number;
  error?: string;
  suggestedName: string | null;
  nameAccepted: boolean | null;
}

interface SampleOption {
  id: number;
  label: string;
}

interface Props {
  experimentId: number;
  samples: SampleOption[];
  onUploaded: () => void;
}

export function ExperimentFileUploader({ experimentId, samples, onUploaded }: Props) {
  const { has } = useCapabilities();
  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<FileItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sampleId, setSampleId] = useState("");
  const [experimentCode, setExperimentCode] = useState<string | null>(null);
  const [projectCode, setProjectCode] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Pull experiment + project codes for filename suggestion
  useEffect(() => {
    let cancelled = false;
    api
      .get<ExperimentDetail>(`/api/experiments/${experimentId}`)
      .then((exp) => {
        if (cancelled) return;
        setExperimentCode(exp.code ?? null);
        if (exp.project) {
          api
            .get<Project>(`/api/projects/${exp.project.id}`)
            .then((proj) => {
              if (!cancelled) setProjectCode(proj.code ?? null);
            })
            .catch(() => {
              if (!cancelled) setProjectCode(null);
            });
        } else {
          setProjectCode(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setExperimentCode(null);
          setProjectCode(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [experimentId]);

  // Recompute suggested names when association changes
  useEffect(() => {
    const smp = sampleId ? samples.find((s) => String(s.id) === sampleId) : null;
    const dateStr = todayDateStr();
    const suggestOpts = {
      projectCode,
      experimentCode,
      sampleId: smp?.label ?? null,
      dateStr,
    };

    setItems((prev) => {
      const updated = prev.map((item) => {
        if (item.status !== "queued") return item;
        const suggested = suggestFilename(item.file.name, suggestOpts);
        // Preserve the accept/dismiss decision when the suggestion is unchanged.
        if (suggested === item.suggestedName) return item;
        return { ...item, suggestedName: suggested, nameAccepted: null };
      });
      return dedupeNames(updated);
    });
  }, [sampleId, samples, projectCode, experimentCode]);

  const addFiles = (incoming: File[]) => {
    const smp = sampleId ? samples.find((s) => String(s.id) === sampleId) : null;
    const dateStr = todayDateStr();
    const suggestOpts = {
      projectCode,
      experimentCode,
      sampleId: smp?.label ?? null,
      dateStr,
    };

    setItems((prev) => {
      const newItems: FileItem[] = incoming.map((f) => ({
        file: f,
        status: "queued",
        progress: 0,
        suggestedName: suggestFilename(f.name, suggestOpts),
        nameAccepted: null,
      }));
      return dedupeNames([...prev, ...newItems]);
    });
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    addFiles(Array.from(e.dataTransfer.files));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(Array.from(e.target.files));
  };

  const setItemState = (idx: number, patch: Partial<FileItem>) => {
    setItems((prev) => prev.map((item, i) => (i === idx ? { ...item, ...patch } : item)));
  };

  const removeItem = (idx: number) => {
    setItems((prev) => prev.filter((_, i) => i !== idx));
  };

  const acceptRename = (idx: number) => setItemState(idx, { nameAccepted: true });
  const rejectRename = (idx: number) => setItemState(idx, { nameAccepted: false });

  const setAllRenames = (accepted: boolean) =>
    setItems((prev) =>
      prev.map((item) =>
        item.status === "queued" && item.suggestedName
          ? { ...item, nameAccepted: accepted }
          : item,
      ),
    );
  const acceptAllRenames = () => setAllRenames(true);
  const dismissAllRenames = () => setAllRenames(false);

  const suggestionCount = items.filter(
    (i) => i.status === "queued" && i.suggestedName,
  ).length;

  const uploadAll = async () => {
    setUploading(true);
    const opts = {
      experimentId,
      sampleId: sampleId ? parseInt(sampleId, 10) : undefined,
    };
    let anySucceeded = false;

    for (let i = 0; i < items.length; i++) {
      if (items[i].status === "complete") continue;
      setItemState(i, { status: "uploading", progress: 0 });
      const item = items[i];
      // Keep the original name by default; only rename when the suggestion was
      // explicitly accepted. Undecided suggestions are dismissed on upload.
      const useFilename =
        item.nameAccepted === true ? item.suggestedName ?? undefined : undefined;
      // Signed direct-to-storage upload when the backend supports it; otherwise
      // the server-proxied path (e.g. NFS, signed_url_upload=False).
      const upload = has("signed_url_upload") ? api.uploadSigned : api.uploadProxied;
      try {
        await upload<FileResponse>(item.file, {
          ...opts,
          filename: useFilename,
          onProgress: (pct) => setItemState(i, { progress: pct }),
        });
        setItemState(i, { status: "complete", progress: 100 });
        anySucceeded = true;
      } catch (err) {
        setItemState(i, {
          status: "error",
          error: err instanceof Error ? err.message : "Upload failed",
        });
      }
    }

    setUploading(false);
    if (anySucceeded) onUploaded();
  };

  const pendingCount = items.filter((i) => i.status !== "complete").length;

  const associationSummary = () => {
    if (sampleId) {
      const s = samples.find((s) => String(s.id) === sampleId);
      return `Sample: ${s?.label ?? sampleId}`;
    }
    return "Whole experiment";
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="px-3 py-1.5 bg-bioaf-600 text-white rounded-md text-sm hover:bg-bioaf-700"
      >
        {expanded ? "Cancel upload" : "Upload"}
      </button>

      {expanded && (
        <div className="basis-full w-full bg-white rounded-lg shadow p-4 space-y-4">
          {/* See data/upload/page.tsx: a <label> keeps the whole area
              click-to-browse while `sr-only` keeps the input focusable, so the
              picker opens on Enter or Space without a key handler. */}
          <label
            onDrop={handleDrop}
            onDragOver={(e) => e.preventDefault()}
            className="block border-2 border-dashed border-gray-300 rounded-lg p-8 text-center cursor-pointer hover:border-blue-400 transition-colors focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-[rgb(var(--color-focus-ring))]"
          >
            <p className="text-gray-500 mb-1">Drag &amp; drop any files here</p>
            <p className="text-sm text-gray-500">or click to browse</p>
            <input
              ref={fileInputRef}
              data-testid="upload-file-input"
              type="file"
              multiple
              onChange={handleFileSelect}
              aria-label="Upload files: drag and drop, or browse"
              className="sr-only"
            />
          </label>

          <div>
            <label
              htmlFor="exp-uploader-sample"
              className="block text-xs font-medium text-gray-600 mb-1"
            >
              Sample (optional)
            </label>
            <select
              id="exp-uploader-sample"
              value={sampleId}
              onChange={(e) => setSampleId(e.target.value)}
              className="w-full sm:w-64 px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
            >
              <option value="">Whole experiment</option>
              {samples.map((s) => (
                <option key={s.id} value={String(s.id)}>
                  {s.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-blue-700 bg-blue-50 rounded px-3 py-1.5 mt-2">
              Files will be associated with: {associationSummary()}
            </p>
          </div>

          {items.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2 gap-3">
                <h3 className="font-medium text-sm">Files ({items.length})</h3>
                {suggestionCount > 0 && (
                  <div className="flex gap-2 shrink-0">
                    <button
                      type="button"
                      onClick={acceptAllRenames}
                      className="px-2 py-0.5 text-xs bg-amber-600 text-white rounded hover:bg-amber-700"
                    >
                      Accept all
                    </button>
                    <button
                      type="button"
                      onClick={dismissAllRenames}
                      className="px-2 py-0.5 text-xs border border-gray-300 text-gray-600 rounded hover:bg-gray-100"
                    >
                      Dismiss all
                    </button>
                  </div>
                )}
              </div>
              {suggestionCount > 0 && (
                <p className="text-xs text-gray-500 mb-2">
                  Original filenames are kept unless you accept a suggestion.
                </p>
              )}
              <ul className="space-y-3">
                {items.map((item, idx) => (
                  <li
                    key={`${item.file.name}-${idx}`}
                    className="text-sm border-b last:border-0 pb-2 last:pb-0"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="truncate flex-1 mr-3 font-mono text-xs">
                        {item.file.name}
                      </span>
                      <span className="text-gray-500 mr-3 shrink-0">
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

                    {item.status === "queued" &&
                      item.suggestedName &&
                      item.nameAccepted === null && (
                        <div className="mt-1.5 flex items-start gap-2 text-xs bg-amber-50 border border-amber-200 rounded px-2.5 py-2">
                          <span className="text-amber-700 shrink-0 mt-0.5">
                            Suggested name:
                          </span>
                          <span className="font-mono text-amber-900 flex-1 break-all">
                            {item.suggestedName}
                          </span>
                          <div className="flex gap-1 shrink-0 ml-1">
                            <button
                              onClick={() => acceptRename(idx)}
                              className="px-2 py-0.5 bg-amber-600 text-white rounded hover:bg-amber-700"
                            >
                              Accept
                            </button>
                            <button
                              onClick={() => rejectRename(idx)}
                              className="px-2 py-0.5 border border-amber-400 text-amber-700 rounded hover:bg-amber-100"
                            >
                              Keep original
                            </button>
                          </div>
                        </div>
                      )}

                    {item.status === "queued" &&
                      item.suggestedName &&
                      item.nameAccepted === true && (
                        <p className="mt-1 text-xs text-green-700 font-mono">
                          Will upload as: {item.suggestedName}
                        </p>
                      )}

                    {item.status === "queued" &&
                      item.suggestedName &&
                      item.nameAccepted === false && (
                        <p className="mt-1 text-xs text-gray-500">
                          Keeping original name.{" "}
                          <button
                            className="underline text-gray-500 hover:text-gray-700"
                            onClick={() => setItemState(idx, { nameAccepted: null })}
                          >
                            Reconsider
                          </button>
                        </p>
                      )}

                    <ProgressBar item={item} />
                    {item.status === "error" && item.error && (
                      <p className="text-xs text-red-600 mt-1">{item.error}</p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {pendingCount > 0 && (
            <button
              onClick={uploadAll}
              disabled={uploading}
              className="px-4 py-2 bg-bioaf-600 text-white rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
            >
              {uploading
                ? "Uploading..."
                : `Upload ${pendingCount} file${pendingCount !== 1 ? "s" : ""}`}
            </button>
          )}
        </div>
      )}
    </>
  );
}

function dedupeNames(items: FileItem[]): FileItem[] {
  const nameCounts = new Map<string, number>();
  for (const item of items) {
    if (item.suggestedName) {
      nameCounts.set(item.suggestedName, (nameCounts.get(item.suggestedName) ?? 0) + 1);
    }
  }
  const nameCounters = new Map<string, number>();
  return items.map((item) => {
    if (!item.suggestedName || (nameCounts.get(item.suggestedName) ?? 0) <= 1) return item;
    const [stem, ext] = splitExtension(item.suggestedName);
    const seq = (nameCounters.get(item.suggestedName) ?? 0) + 1;
    nameCounters.set(item.suggestedName, seq);
    return { ...item, suggestedName: `${stem}_${String(seq).padStart(3, "0")}${ext}` };
  });
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
      <span className="text-xs font-medium text-bioaf-600 flex items-center gap-1 shrink-0">
        <span className="inline-block h-1.5 w-1.5 bg-bioaf-600 rounded-full animate-pulse" />
        {item.progress}%
      </span>
    );
  }
  return <span className="text-xs text-gray-500 shrink-0">Queued</span>;
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
    <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden mt-1">
      <div
        className={`${barColor} h-1.5 rounded-full transition-all duration-300`}
        style={{ width: `${item.status === "error" ? 100 : item.progress}%` }}
      />
    </div>
  );
}
