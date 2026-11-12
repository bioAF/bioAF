"use client";

import { useMemo } from "react";
import { DataExportDialog, formatExportSize } from "@/components/exports/DataExportDialog";
import { projectExportTarget, type ProjectExportEstimate } from "@/components/exports/exportTargets";

interface ProjectExportModalProps {
  projectId: number;
  projectName: string;
  isOpen: boolean;
  onClose: () => void;
}

function ExperimentBreakdown({ estimate }: { estimate: ProjectExportEstimate }) {
  if (estimate.experiments.length === 0) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">By experiment</p>
      {estimate.experiments.map((exp) => (
        <div key={exp.experiment_id} className="flex justify-between text-xs text-gray-600">
          <span className="truncate max-w-xs">{exp.name}</span>
          <span className="shrink-0 ml-2">{formatExportSize(exp.total_bytes)}</span>
        </div>
      ))}
    </div>
  );
}

export function ProjectExportModal({ projectId, projectName, isOpen, onClose }: ProjectExportModalProps) {
  const target = useMemo(() => projectExportTarget(projectId), [projectId]);
  return (
    <DataExportDialog
      title="Export Project Data"
      entityName={projectName}
      target={target}
      isOpen={isOpen}
      onClose={onClose}
      renderBreakdown={(estimate) => <ExperimentBreakdown estimate={estimate} />}
    />
  );
}
