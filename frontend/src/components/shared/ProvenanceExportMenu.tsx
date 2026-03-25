"use client";

import { useState } from "react";
import { api } from "@/lib/api";

type EntityType = "experiments" | "projects" | "samples" | "pipeline-runs" | "files";
type ExportFormat = "json" | "csv" | "pdf";

interface ProvenanceExportMenuProps {
  entityType: EntityType;
  entityId: number;
}

const FORMAT_LABELS: Record<ExportFormat, string> = {
  json: "JSON",
  csv: "CSV",
  pdf: "PDF",
};

export function ProvenanceExportMenu({ entityType, entityId }: ProvenanceExportMenuProps) {
  const [open, setOpen] = useState(false);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);

  async function handleExport(format: ExportFormat) {
    setExporting(format);
    try {
      await api.download(`/api/${entityType}/${entityId}/provenance/report?format=${format}`);
    } catch (err) {
      console.error("Provenance export failed:", err);
    } finally {
      setExporting(null);
      setOpen(false);
    }
  }

  return (
    <div className="relative inline-block">
      <button
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1.5 border px-3 py-1.5 rounded text-sm hover:bg-gray-50"
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
            d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
          />
        </svg>
        Export Provenance
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-36 bg-white border rounded-md shadow-lg py-1">
            {(["json", "csv", "pdf"] as ExportFormat[]).map((format) => (
              <button
                key={format}
                onClick={() => handleExport(format)}
                disabled={exporting !== null}
                className="w-full text-left px-4 py-2 text-sm hover:bg-gray-100 disabled:opacity-50"
              >
                {exporting === format ? "Exporting..." : FORMAT_LABELS[format]}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
