"use client";

import { useState, useMemo, useCallback } from "react";
import type { FileResponse } from "@/lib/types";

const LARGE_FILE_EXTENSIONS = [".fastq", ".fastq.gz", ".bam", ".bai", ".cram"];

function isLargeFormat(filename: string): boolean {
  const lower = filename.toLowerCase();
  return LARGE_FILE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(1)} ${units[i]}`;
}

function gcsSubpath(gcsUri: string): string {
  // Extract the meaningful subdirectory from a GCS URI
  // e.g., gs://bucket/experiments/1/pipeline-runs/6/star/001/Gene/filtered/barcodes.tsv.gz
  //     -> star/001/Gene/filtered
  const parts = gcsUri.replace(/^gs:\/\/[^/]+\//, "").split("/");
  // Drop the filename (last part)
  parts.pop();
  // Find the first meaningful directory after pipeline-runs/{id}/
  const prIdx = parts.indexOf("pipeline-runs");
  if (prIdx >= 0 && prIdx + 2 < parts.length) {
    return parts.slice(prIdx + 2).join("/");
  }
  // For non-pipeline files, show last 2-3 dirs
  if (parts.length > 3) {
    return parts.slice(-3).join("/");
  }
  return parts.join("/");
}

const SOURCE_LABELS: Record<string, string> = {
  pipeline_output: "Pipeline",
  notebook_output: "Notebook",
  upload: "Upload",
};

function sourceLabel(sourceType: string): string {
  return SOURCE_LABELS[sourceType] || sourceType;
}

const SOURCE_COLORS: Record<string, string> = {
  pipeline_output: "bg-purple-100 text-purple-700",
  notebook_output: "bg-teal-100 text-teal-700",
  upload: "bg-gray-100 text-gray-600",
};

interface FileTreeSelectorProps {
  files: FileResponse[];
  sampleNames: Record<number, string>;
  onSelectionChange: (fileIds: number[]) => void;
}

interface SourceSubgroup {
  key: string;
  label: string;
  files: FileResponse[];
}

interface SampleGroup {
  sampleId: number;
  sampleName: string;
  files: FileResponse[];
  subgroups: SourceSubgroup[];
}

export function FileTreeSelector({ files, sampleNames, onSelectionChange }: FileTreeSelectorProps) {
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showLargeFiles, setShowLargeFiles] = useState(false);
  const [expandedSamples, setExpandedSamples] = useState<Set<number | string>>(new Set());

  const sampleGroups = useMemo((): SampleGroup[] => {
    const groupMap = new Map<number, FileResponse[]>();
    const ungrouped: FileResponse[] = [];
    // Track files already assigned to a sample to avoid showing them
    // again under "Experiment Files"
    const sampleLinkedIds = new Set<number>();

    for (const file of files) {
      if (file.sample_ids && file.sample_ids.length > 0) {
        sampleLinkedIds.add(file.id);
        for (const sampleId of file.sample_ids) {
          const existing = groupMap.get(sampleId) || [];
          existing.push(file);
          groupMap.set(sampleId, existing);
        }
      } else {
        ungrouped.push(file);
      }
    }

    function buildSubgroups(groupFiles: FileResponse[]): SourceSubgroup[] {
      const subMap = new Map<string, FileResponse[]>();
      for (const f of groupFiles) {
        const path = f.gcs_uri ? gcsSubpath(f.gcs_uri) : sourceLabel(f.source_type);
        const existing = subMap.get(path) || [];
        existing.push(f);
        subMap.set(path, existing);
      }
      return Array.from(subMap.entries())
        .map(([key, subFiles]) => ({ key, label: key || "Other", files: subFiles }))
        .sort((a, b) => a.label.localeCompare(b.label));
    }

    const groups: SampleGroup[] = [];
    for (const [sampleId, sampleFiles] of groupMap) {
      groups.push({
        sampleId,
        sampleName: sampleNames[sampleId] || `Sample ${sampleId}`,
        files: sampleFiles,
        subgroups: buildSubgroups(sampleFiles),
      });
    }

    // Only show experiment-level files that are not already under a sample
    const dedupedUngrouped = ungrouped.filter((f) => !sampleLinkedIds.has(f.id));
    if (dedupedUngrouped.length > 0) {
      groups.push({
        sampleId: 0,
        sampleName: "Experiment Files",
        files: dedupedUngrouped,
        subgroups: buildSubgroups(dedupedUngrouped),
      });
    }

    return groups;
  }, [files, sampleNames]);

  // Auto-expand all on first render
  useMemo(() => {
    const allIds = new Set<number | string>(sampleGroups.map((g) => g.sampleId));
    allIds.add("root");
    setExpandedSamples(allIds);
  }, [sampleGroups]);

  const visibleFiles = useCallback(
    (groupFiles: FileResponse[]) =>
      groupFiles.filter((f) => showLargeFiles || !isLargeFormat(f.filename)),
    [showLargeFiles]
  );

  const allVisibleFiles = useMemo(
    () => files.filter((f) => showLargeFiles || !isLargeFormat(f.filename)),
    [files, showLargeFiles]
  );

  const totalSelectedSize = useMemo(() => {
    let total = 0;
    for (const file of files) {
      if (selectedIds.has(file.id) && file.size_bytes) {
        total += file.size_bytes;
      }
    }
    return total;
  }, [selectedIds, files]);

  const updateSelection = useCallback(
    (newIds: Set<number>) => {
      setSelectedIds(newIds);
      onSelectionChange(Array.from(newIds));
    },
    [onSelectionChange]
  );

  const toggleFile = useCallback(
    (fileId: number) => {
      const next = new Set(selectedIds);
      if (next.has(fileId)) {
        next.delete(fileId);
      } else {
        next.add(fileId);
      }
      updateSelection(next);
    },
    [selectedIds, updateSelection]
  );

  const toggleSample = useCallback(
    (group: SampleGroup) => {
      const visible = visibleFiles(group.files);
      const visibleIds = visible.map((f) => f.id);
      const allSelected = visibleIds.every((id) => selectedIds.has(id));

      const next = new Set(selectedIds);
      if (allSelected) {
        for (const id of visibleIds) next.delete(id);
      } else {
        for (const id of visibleIds) next.add(id);
      }
      updateSelection(next);
    },
    [selectedIds, visibleFiles, updateSelection]
  );

  const toggleAll = useCallback(() => {
    const allIds = allVisibleFiles.map((f) => f.id);
    const allSelected = allIds.every((id) => selectedIds.has(id));

    const next = new Set(selectedIds);
    if (allSelected) {
      for (const id of allIds) next.delete(id);
    } else {
      for (const id of allIds) next.add(id);
    }
    updateSelection(next);
  }, [selectedIds, allVisibleFiles, updateSelection]);

  const toggleExpand = useCallback((key: number | string) => {
    setExpandedSamples((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  if (files.length === 0) {
    return <div className="text-sm text-gray-400 py-4">No files available</div>;
  }

  const allSelected = allVisibleFiles.length > 0 && allVisibleFiles.every((f) => selectedIds.has(f.id));
  const someSelected = allVisibleFiles.some((f) => selectedIds.has(f.id));
  const rootExpanded = expandedSamples.has("root");

  return (
    <div className="border rounded-lg p-4">
      <div className="flex items-center gap-2 mb-3">
        <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={showLargeFiles}
            onChange={(e) => setShowLargeFiles(e.target.checked)}
            aria-label="Include FASTQ and BAM files"
          />
          Include FASTQ and BAM files
        </label>
      </div>

      {/* Root level: Select All */}
      <div className="flex items-center gap-2 py-1 mb-1">
        <button
          type="button"
          onClick={() => toggleExpand("root")}
          className="text-xs text-gray-400 w-4"
        >
          {rootExpanded ? "\u25BC" : "\u25B6"}
        </button>
        <input
          type="checkbox"
          checked={allSelected}
          ref={(el) => {
            if (el) el.indeterminate = someSelected && !allSelected;
          }}
          onChange={toggleAll}
          aria-label="Select all files"
        />
        <span className="text-sm font-semibold">All Files</span>
        <span className="text-xs text-gray-400">({allVisibleFiles.length} files)</span>
      </div>

      {rootExpanded && (
        <div className="ml-4 space-y-1">
          {sampleGroups.map((group) => {
            const visible = visibleFiles(group.files);
            if (visible.length === 0) return null;

            const groupAllSelected = visible.every((f) => selectedIds.has(f.id));
            const groupSomeSelected = visible.some((f) => selectedIds.has(f.id));
            const isExpanded = expandedSamples.has(group.sampleId);

            return (
              <div key={group.sampleId} className="ml-2">
                <div className="flex items-center gap-2 py-1">
                  <button
                    type="button"
                    onClick={() => toggleExpand(group.sampleId)}
                    className="text-xs text-gray-400 w-4"
                  >
                    {isExpanded ? "\u25BC" : "\u25B6"}
                  </button>
                  <input
                    type="checkbox"
                    checked={groupAllSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = groupSomeSelected && !groupAllSelected;
                    }}
                    onChange={() => toggleSample(group)}
                    aria-label={group.sampleName}
                  />
                  <span className="text-sm font-medium">{group.sampleName}</span>
                  <span className="text-xs text-gray-400">({visible.length} files)</span>
                </div>

                {isExpanded && (
                  <div className="ml-8 space-y-1">
                    {group.subgroups.map((sub) => {
                      const subVisible = sub.files.filter(
                        (f) => showLargeFiles || !isLargeFormat(f.filename)
                      );
                      if (subVisible.length === 0) return null;
                      return (
                        <div key={sub.key} className="ml-1">
                          <div className="text-xs text-gray-400 font-mono py-0.5 border-l-2 border-gray-200 pl-2 mb-0.5">
                            {sub.label}
                          </div>
                          <div className="ml-3 space-y-0.5">
                            {subVisible.map((file) => (
                              <label
                                key={file.id}
                                className="flex items-center gap-2 py-0.5 text-sm cursor-pointer hover:bg-gray-50 rounded px-1"
                              >
                                <input
                                  type="checkbox"
                                  checked={selectedIds.has(file.id)}
                                  onChange={() => toggleFile(file.id)}
                                  aria-label={file.filename}
                                />
                                <span>{file.filename}</span>
                                <span className={`text-[10px] px-1.5 py-0.5 rounded ${SOURCE_COLORS[file.source_type] || "bg-gray-100 text-gray-500"}`}>
                                  {sourceLabel(file.source_type)}
                                </span>
                                {file.size_bytes != null && (
                                  <span className="text-xs text-gray-400">
                                    ({formatBytes(file.size_bytes)})
                                  </span>
                                )}
                                <span className="text-[10px] text-gray-300">
                                  {new Date(file.created_at).toLocaleDateString()}
                                </span>
                              </label>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {selectedIds.size > 0 && (
        <div className="mt-3 pt-3 border-t text-sm">
          <span className="text-gray-600">
            {selectedIds.size} file{selectedIds.size !== 1 ? "s" : ""} selected:{" "}
            {formatBytes(totalSelectedSize)}
          </span>
          {totalSelectedSize > 10_737_418_240 && (
            <span className="ml-2 text-amber-600 font-medium">
              Warning: selection exceeds 10 GB
            </span>
          )}
        </div>
      )}
    </div>
  );
}
