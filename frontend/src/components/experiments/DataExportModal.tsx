"use client";

import { useMemo } from "react";
import { DataExportDialog, formatExportSize } from "@/components/exports/DataExportDialog";
import { experimentExportTarget, type ExportEstimate } from "@/components/exports/exportTargets";

interface DataExportModalProps {
  experimentId: number;
  experimentName: string;
  isOpen: boolean;
  onClose: () => void;
}

function FileTypeBreakdown({ estimate }: { estimate: ExportEstimate }) {
  return (
    <>
      {Object.entries(estimate.breakdown).map(([type, size]) => (
        <div key={type} className="flex justify-between text-xs text-gray-600">
          <span className="capitalize">{type}</span>
          <span>{formatExportSize(size)}</span>
        </div>
      ))}
    </>
  );
}

export function DataExportModal({ experimentId, experimentName, isOpen, onClose }: DataExportModalProps) {
  const target = useMemo(() => experimentExportTarget(experimentId), [experimentId]);
  return (
    <DataExportDialog
      title="Export Data"
      entityName={experimentName}
      target={target}
      isOpen={isOpen}
      onClose={onClose}
      renderBreakdown={(estimate) => <FileTypeBreakdown estimate={estimate} />}
    />
  );
}
