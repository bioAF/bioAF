"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";
import { suggestFilename, splitExtension, todayDateStr } from "@/lib/fileNaming";
import type {
  ExperimentListResponse,
  FileResponse,
  ProjectListResponse,
  SampleBrief,
} from "@/lib/types";

interface ProjectOption {
  id: number;
  name: string;
  code: string | null;
}

interface ExperimentOption {
  id: number;
  name: string;
  code: string | null;
  status: string;
}

interface SampleOption {
  id: number;
  label: string;
}

type FileStatus = "queued" | "uploading" | "complete" | "error";

interface FileItem {
  file: File;
  status: FileStatus;
  progress: number;
  error?: string;
  // rename suggestion state
  suggestedName: string | null;
  nameAccepted: boolean | null; // null = undecided
}

export default function DataUploadPage() {
  const [items, setItems] = useState<FileItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [experiments, setExperiments] = useState<ExperimentOption[]>([]);
  const [samples, setSamples] = useState<SampleOption[]>([]);

  type Scope = "global" | "project" | "experiment" | "sample";
  const [scope, setScope] = useState<Scope>("experiment");
  const [projectId, setProjectId] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [sampleId, setSampleId] = useState("");

  // Clear lower-level selections when scope narrows
  useEffect(() => {
    if (scope === "global") {
      setProjectId("");
      setExperimentId("");
      setSampleId("");
    } else if (scope === "project") {
      setExperimentId("");
      setSampleId("");
    } else if (scope === "experiment") {
      setSampleId("");
    }
  }, [scope]);

  const scopeReady = (() => {
    if (scope === "global") return true;
    if (scope === "project") return !!projectId;
    if (scope === "experiment") return !!experimentId;
    if (scope === "sample") return !!sampleId;
    return false;
  })();

  // Load projects on mount
  useEffect(() => {
    api
      .get<ProjectListResponse>("/api/projects?page_size=100")
      .then((data) =>
        setProjects(data.projects.map((p) => ({ id: p.id, name: p.name, code: p.code ?? null }))),
      )
      .catch(() => setProjects([]));
  }, []);

  // Reload experiments when project changes
  useEffect(() => {
    setExperimentId("");
    setSampleId("");
    setSamples([]);
    const qs = projectId ? `?project_id=${projectId}&page_size=100` : "?page_size=100";
    api
      .get<ExperimentListResponse>(`/api/experiments${qs}`)
      .then((data) =>
        setExperiments(
          data.experiments.map((e) => ({ id: e.id, name: e.name, code: e.code ?? null, status: e.status })),
        ),
      )
      .catch(() => setExperiments([]));
  }, [projectId]);

  // Load samples when experiment changes
  useEffect(() => {
    setSampleId("");
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
            label: s.sample_id_unique ?? `Sample #${s.id}`,
          })),
        ),
      )
      .catch(() => setSamples([]));
  }, [experimentId]);

  // Recompute suggested names whenever association changes
  useEffect(() => {
    const proj = projects.find((p) => String(p.id) === projectId);
    const exp = experiments.find((e) => String(e.id) === experimentId);
    const smp = sampleId
      ? samples.find((s) => String(s.id) === sampleId)
      : null;

    const dateStr = todayDateStr();

    setItems((prev) => {
      const suggestOpts = {
        projectCode: proj?.code ?? null,
        experimentCode: exp?.code ?? null,
        sampleId: smp?.label ?? null,
        dateStr,
      };

      // First pass: generate suggested names
      const updated = prev.map((item) => {
        if (item.status !== "queued") return item;
        const suggested = suggestFilename(item.file.name, suggestOpts);
        return { ...item, suggestedName: suggested, nameAccepted: null };
      });

      // Second pass: deduplicate -- append sequence number when names collide
      const nameCounts = new Map<string, number>();
      for (const item of updated) {
        if (item.suggestedName) {
          nameCounts.set(item.suggestedName, (nameCounts.get(item.suggestedName) ?? 0) + 1);
        }
      }
      const nameCounters = new Map<string, number>();
      return updated.map((item) => {
        if (!item.suggestedName || (nameCounts.get(item.suggestedName) ?? 0) <= 1) return item;
        const [stem, ext] = splitExtension(item.suggestedName);
        const seq = (nameCounters.get(item.suggestedName) ?? 0) + 1;
        nameCounters.set(item.suggestedName, seq);
        return { ...item, suggestedName: `${stem}_${String(seq).padStart(3, "0")}${ext}` };
      });
    });
  }, [projectId, experimentId, sampleId, projects, experiments, samples]);

  const addFiles = (incoming: File[]) => {
    const accepted = incoming;

    const proj = projects.find((p) => String(p.id) === projectId);
    const exp = experiments.find((e) => String(e.id) === experimentId);
    const smp = sampleId ? samples.find((s) => String(s.id) === sampleId) : null;
    const dateStr = todayDateStr();

    setItems((prev) => {
      const suggestOpts = {
        projectCode: proj?.code ?? null,
        experimentCode: exp?.code ?? null,
        sampleId: smp?.label ?? null,
        dateStr,
      };

      const newItems = accepted.map((f) => ({
        file: f,
        status: "queued" as FileStatus,
        progress: 0,
        suggestedName: suggestFilename(f.name, suggestOpts),
        nameAccepted: null as boolean | null,
      }));

      // Deduplicate across existing + new items
      const all = [...prev, ...newItems];
      const nameCounts = new Map<string, number>();
      for (const item of all) {
        if (item.suggestedName) {
          nameCounts.set(item.suggestedName, (nameCounts.get(item.suggestedName) ?? 0) + 1);
        }
      }
      const nameCounters = new Map<string, number>();
      return all.map((item) => {
        if (!item.suggestedName || (nameCounts.get(item.suggestedName) ?? 0) <= 1) return item;
        const [stem, ext] = splitExtension(item.suggestedName);
        const seq = (nameCounters.get(item.suggestedName) ?? 0) + 1;
        nameCounters.set(item.suggestedName, seq);
        return { ...item, suggestedName: `${stem}_${String(seq).padStart(3, "0")}${ext}` };
      });
    });
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    addFiles(Array.from(e.dataTransfer.files));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(Array.from(e.target.files));
  };

  const removeItem = (idx: number) => {
    setItems((prev) => prev.filter((_, i) => i !== idx));
  };

  const setItemState = (idx: number, patch: Partial<FileItem>) => {
    setItems((prev) => prev.map((item, i) => (i === idx ? { ...item, ...patch } : item)));
  };

  const acceptRename = (idx: number) => setItemState(idx, { nameAccepted: true });
  const rejectRename = (idx: number) => setItemState(idx, { nameAccepted: false });

  const uploadAll = async () => {
    setUploading(true);
    const opts = {
      projectId: scope === "global" ? undefined : projectId ? parseInt(projectId, 10) : undefined,
      experimentId: scope === "global" || scope === "project" ? undefined : experimentId ? parseInt(experimentId, 10) : undefined,
      sampleId: scope === "sample" && sampleId ? parseInt(sampleId, 10) : undefined,
      isGlobal: scope === "global",
    };

    for (let i = 0; i < items.length; i++) {
      if (items[i].status === "complete") continue;

      setItemState(i, { status: "uploading", progress: 0 });

      const item = items[i];
      // Use accepted suggested name; if undecided with a suggestion, accept by default
      const useFilename =
        item.nameAccepted === false
          ? undefined // keep original (don't pass override)
          : item.suggestedName ?? undefined;

      try {
        await api.uploadSigned<FileResponse>(item.file, {
          ...opts,
          filename: useFilename,
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

  const associationSummary = () => {
    if (scope === "global") return "Global (visible to anyone in your organization)";
    if (scope === "sample" && sampleId) {
      const s = samples.find((s) => String(s.id) === sampleId);
      const e = experiments.find((e) => String(e.id) === experimentId);
      return `Sample: ${s?.label ?? sampleId} (${e?.name ?? experimentId})`;
    }
    if (scope === "experiment" && experimentId) {
      const e = experiments.find((e) => String(e.id) === experimentId);
      return `Experiment: ${e?.name ?? experimentId}`;
    }
    if (scope === "project" && projectId) {
      const p = projects.find((p) => String(p.id) === projectId);
      return `Project: ${p?.name ?? projectId}`;
    }
    return null;
  };

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
                Drag & drop any files here
              </p>
              <p className="text-sm text-gray-400">or click to browse</p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                onChange={handleFileSelect}
                className="hidden"
              />
            </div>

            {/* Association selectors */}
            <div className="bg-white rounded-lg shadow p-4 space-y-4">
              <h3 className="font-medium text-gray-700">Association</h3>
              <p className="text-xs text-gray-500">
                Choose where these files belong. Global files are visible to anyone in
                your organization but are not tied to any project, experiment, or sample.
              </p>

              <div>
                <label htmlFor="upload-scope-select" className="block text-xs font-medium text-gray-600 mb-1">
                  Scope
                </label>
                <select
                  id="upload-scope-select"
                  value={scope}
                  onChange={(e) => setScope(e.target.value as Scope)}
                  className="w-full sm:w-64 px-3 py-2 border border-gray-300 rounded-md text-sm bg-white"
                >
                  <option value="global">Global</option>
                  <option value="project">Project</option>
                  <option value="experiment">Experiment</option>
                  <option value="sample">Sample</option>
                </select>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {/* Project */}
                <div>
                  <label htmlFor="upload-project-select" className="block text-xs font-medium text-gray-600 mb-1">
                    Project {scope === "project" && <span className="text-red-500">*</span>}
                  </label>
                  <select
                    id="upload-project-select"
                    value={projectId}
                    onChange={(e) => setProjectId(e.target.value)}
                    disabled={scope === "global"}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white disabled:bg-gray-50 disabled:text-gray-400"
                  >
                    <option value="">{scope === "global" ? "N/A (global)" : "Select project"}</option>
                    {projects.map((p) => (
                      <option key={p.id} value={String(p.id)}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Experiment */}
                <div>
                  <label htmlFor="upload-experiment-select" className="block text-xs font-medium text-gray-600 mb-1">
                    Experiment {scope === "experiment" && <span className="text-red-500">*</span>}
                  </label>
                  <select
                    id="upload-experiment-select"
                    value={experimentId}
                    onChange={(e) => setExperimentId(e.target.value)}
                    disabled={scope === "global" || scope === "project"}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white disabled:bg-gray-50 disabled:text-gray-400"
                  >
                    <option value="">
                      {scope === "global" || scope === "project" ? "N/A" : "Select experiment"}
                    </option>
                    {experiments.map((exp) => (
                      <option key={exp.id} value={String(exp.id)}>
                        {exp.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Sample */}
                <div>
                  <label htmlFor="upload-sample-select" className="block text-xs font-medium text-gray-600 mb-1">
                    Sample {scope === "sample" && <span className="text-red-500">*</span>}
                  </label>
                  <select
                    id="upload-sample-select"
                    value={sampleId}
                    onChange={(e) => setSampleId(e.target.value)}
                    disabled={scope !== "sample" || !experimentId}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm bg-white disabled:bg-gray-50 disabled:text-gray-400"
                  >
                    <option value="">{scope === "sample" ? "Select sample" : "N/A"}</option>
                    {samples.map((s) => (
                      <option key={s.id} value={String(s.id)}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                  {scope === "sample" && !experimentId && (
                    <p className="text-xs text-gray-400 mt-1">Select an experiment first</p>
                  )}
                </div>
              </div>

              {associationSummary() ? (
                <p className="text-xs text-blue-700 bg-blue-50 rounded px-3 py-1.5">
                  Files will be associated with: {associationSummary()}
                </p>
              ) : (
                <p className="text-xs text-amber-700 bg-amber-50 rounded px-3 py-1.5">
                  Pick a {scope} below before uploading.
                </p>
              )}
            </div>

            {/* File list */}
            {items.length > 0 && (
              <div className="bg-white rounded-lg shadow p-4">
                <h3 className="font-medium mb-3">Files ({items.length})</h3>
                <ul className="space-y-4">
                  {items.map((item, idx) => (
                    <li key={`${item.file.name}-${idx}`} className="text-sm border-b last:border-0 pb-3 last:pb-0">
                      <div className="flex items-center justify-between mb-1">
                        <span className="truncate flex-1 mr-3 font-mono text-xs">
                          {item.file.name}
                        </span>
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

                      {/* Advisory rename suggestion */}
                      {item.status === "queued" && item.suggestedName && item.nameAccepted === null && (
                        <div className="mt-1.5 flex items-start gap-2 text-xs bg-amber-50 border border-amber-200 rounded px-2.5 py-2">
                          <span className="text-amber-700 shrink-0 mt-0.5">Suggested name:</span>
                          <span className="font-mono text-amber-900 flex-1 break-all">{item.suggestedName}</span>
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

                      {item.status === "queued" && item.suggestedName && item.nameAccepted === true && (
                        <p className="mt-1 text-xs text-green-700 font-mono">
                          Will upload as: {item.suggestedName}
                        </p>
                      )}

                      {item.status === "queued" && item.suggestedName && item.nameAccepted === false && (
                        <p className="mt-1 text-xs text-gray-400">
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

                {pendingCount > 0 && (
                  <button
                    onClick={uploadAll}
                    disabled={uploading || !scopeReady}
                    title={!scopeReady ? `Pick a ${scope} first` : undefined}
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
    <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden mt-1">
      <div
        className={`${barColor} h-1.5 rounded-full transition-all duration-300`}
        style={{ width: `${item.status === "error" ? 100 : item.progress}%` }}
      />
    </div>
  );
}
