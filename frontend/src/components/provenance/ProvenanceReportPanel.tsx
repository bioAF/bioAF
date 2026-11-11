"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";

type EntityType = "project" | "experiment" | "sample" | "pipeline_run" | "artifact";
type DownloadFormat = "json" | "md" | "pdf" | "csv" | "all";

interface ProvenanceReportPanelProps {
  entityType: EntityType;
  entityId: number;
  entityName: string;
}

const ENTITY_PATH_MAP: Record<EntityType, string> = {
  project: "projects",
  experiment: "experiments",
  sample: "samples",
  pipeline_run: "pipeline-runs",
  artifact: "files",
};

const FORMAT_LABELS: Record<DownloadFormat, string> = {
  json: "JSON",
  md: "Markdown",
  pdf: "PDF",
  csv: "CSV",
  all: "All Formats",
};

/** One line of the preview: an app-authored label and a metadata value. */
interface PreviewLine {
  label?: string;
  value: string;
}

interface PreviewSection {
  heading: string;
  level: 2 | 3;
  lines: PreviewLine[];
}

interface PreviewModel {
  title: string;
  header: PreviewLine[];
  sections: PreviewSection[];
}

type PreviewState = { model: PreviewModel } | { failed: true };

function titleCase(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * The preview's structure, read from the report's JSON. Every name, filename
 * and value stays a string that is rendered as text, so metadata shaped like
 * Markdown or HTML can never become a link, an image, markup or a heading.
 */
function buildPreview(report: Record<string, unknown>, entityName: string): PreviewModel {
  const model: PreviewModel = {
    title: `Provenance Report: ${entityName}`,
    header: [
      { label: "Type", value: String(report.report_type) },
      { label: "Schema Version", value: String(report.schema_version) },
      { label: "Generated", value: new Date(report.generated_at as string).toLocaleString() },
    ],
    sections: [],
  };

  const entity = report.entity as Record<string, unknown> | undefined;
  if (entity) {
    // Separate scalar fields from nested collections
    const scalarLines: PreviewLine[] = [];
    const collectionLines: PreviewLine[] = [];

    for (const [key, val] of Object.entries(entity)) {
      if (val == null || val === "") continue;
      // Skip source -- rendered separately below
      if (key === "source") continue;

      if (Array.isArray(val)) {
        collectionLines.push({ label: titleCase(key), value: `${val.length} item${val.length !== 1 ? "s" : ""}` });
      } else if (typeof val === "object") {
        const obj = val as Record<string, unknown>;
        if (obj.email) {
          scalarLines.push({ label: key, value: String(obj.name || obj.email) });
        } else if (obj.raw !== undefined || obj.results !== undefined) {
          const rawCount = Array.isArray(obj.raw) ? obj.raw.length : 0;
          const resCount = Array.isArray(obj.results) ? obj.results.length : 0;
          collectionLines.push({ label: "Files", value: `${rawCount} raw, ${resCount} results` });
        } else if (obj.files !== undefined || obj.samples !== undefined) {
          for (const [subKey, subVal] of Object.entries(obj)) {
            if (Array.isArray(subVal)) {
              collectionLines.push({ label: titleCase(subKey), value: String(subVal.length) });
            }
          }
        }
      } else {
        scalarLines.push({ label: key, value: String(val) });
      }
    }

    if (scalarLines.length > 0) {
      model.sections.push({ heading: "Entity Details", level: 2, lines: scalarLines });
    }

    // Render source section for artifacts
    const source = entity.source as Record<string, unknown> | undefined;
    if (source) {
      const sourceLines: PreviewLine[] = [{ label: "Source Type", value: String(source.type) }];
      const subsections: PreviewSection[] = [];

      const ns = source.notebook_session as Record<string, unknown> | undefined;
      if (ns) {
        sourceLines.push({ label: "Session ID", value: String(ns.id) });
        sourceLines.push({ label: "Session Type", value: String(ns.session_type) });
        sourceLines.push({ label: "Status", value: String(ns.status) });
        sourceLines.push({ label: "Resources", value: `${ns.cpu_cores} CPU, ${ns.memory_gb} GB` });
        if (ns.started_at) sourceLines.push({ label: "Started", value: new Date(ns.started_at as string).toLocaleString() });
        if (ns.stopped_at) sourceLines.push({ label: "Stopped", value: new Date(ns.stopped_at as string).toLocaleString() });
        if (ns.git_branch_name) sourceLines.push({ label: "Git Branch", value: String(ns.git_branch_name) });
        if (ns.git_commit_hash) sourceLines.push({ label: "Git Commit", value: String(ns.git_commit_hash) });

        const env = ns.environment as Record<string, unknown> | undefined;
        if (env) {
          const envLines: PreviewLine[] = [
            { label: "Name", value: String(env.environment_name) },
            { label: "Version", value: `v${env.version_number}.${env.build_number}` },
            { label: "Format", value: String(env.definition_format) },
          ];
          if (env.image_uri) envLines.push({ label: "Image", value: String(env.image_uri) });
          subsections.push({ heading: "Environment", level: 3, lines: envLines });
        }

        const inputFiles = ns.input_files as Array<Record<string, unknown>> | undefined;
        if (inputFiles && inputFiles.length > 0) {
          subsections.push({
            heading: `Input Files (${inputFiles.length})`,
            level: 3,
            lines: inputFiles.map((f) => ({ value: `${f.filename} (${f.file_type})` })),
          });
        }
      }

      const pr = source.pipeline_run as Record<string, unknown> | undefined;
      if (pr) {
        sourceLines.push({ label: "Pipeline", value: String(pr.pipeline_name) });
        if (pr.pipeline_version) sourceLines.push({ label: "Version", value: String(pr.pipeline_version) });
        sourceLines.push({ label: "Run ID", value: String(pr.id) });
        sourceLines.push({ label: "Status", value: String(pr.status) });
      }

      model.sections.push({ heading: "Source", level: 2, lines: sourceLines }, ...subsections);
    }

    if (collectionLines.length > 0) {
      model.sections.push({ heading: "Related Data", level: 2, lines: collectionLines });
    }
  }

  const auditTrail = report.audit_trail as unknown[] | undefined;
  if (Array.isArray(auditTrail) && auditTrail.length > 0) {
    model.sections.push({ heading: "Audit Trail", level: 2, lines: [{ value: `${auditTrail.length} entries` }] });
  }

  return model;
}

function PreviewLines({ lines }: { lines: PreviewLine[] }) {
  return (
    <ul>
      {lines.map((line, i) => (
        <li key={i}>
          {line.label !== undefined && <strong>{line.label}:</strong>} {line.value}
        </li>
      ))}
    </ul>
  );
}

function ProvenancePreview({ model }: { model: PreviewModel }) {
  return (
    <>
      <h1>{model.title}</h1>
      {model.header.map((line) => (
        <p key={line.label} className="my-0">
          <strong>{line.label}:</strong> {line.value}
        </p>
      ))}
      {model.sections.map((section) => {
        const Heading = section.level === 2 ? "h2" : "h3";
        return (
          <section key={section.heading}>
            <Heading>{section.heading}</Heading>
            <PreviewLines lines={section.lines} />
          </section>
        );
      })}
    </>
  );
}

export function ProvenanceReportPanel({ entityType, entityId, entityName }: ProvenanceReportPanelProps) {
  const { canAccess, loading: permLoading } = usePermissions();
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  useDismissOnEscape(dropdownOpen, () => { setDropdownOpen(false); });
  const [downloading, setDownloading] = useState<DownloadFormat | null>(null);

  const basePath = `/api/${ENTITY_PATH_MAP[entityType]}/${entityId}/provenance/report`;

  if (permLoading || !canAccess("files", "download")) {
    return null;
  }

  const handlePreview = async () => {
    if (preview !== null) {
      setPreview(null);
      return;
    }
    setLoadingPreview(true);
    try {
      const report = await api.get<Record<string, unknown>>(`${basePath}?format=json`);
      setPreview({ model: buildPreview(report, entityName) });
    } catch (err) {
      console.error("Failed to load provenance preview:", err);
      setPreview({ failed: true });
    } finally {
      setLoadingPreview(false);
    }
  };

  const handleDownload = async (format: DownloadFormat) => {
    setDownloading(format);
    try {
      await api.download(`${basePath}?format=${format}`);
    } catch (err) {
      console.error("Provenance download failed:", err);
    } finally {
      setDownloading(null);
      setDropdownOpen(false);
    }
  };

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-sm font-semibold text-gray-700 mb-4">Provenance Report</h3>
      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={handlePreview}
          disabled={loadingPreview}
          className="inline-flex items-center gap-1.5 border px-3 py-1.5 rounded text-sm hover:bg-gray-50 disabled:opacity-50"
        >
          {loadingPreview ? "Loading..." : preview !== null ? "Hide Preview" : "Preview Report"}
        </button>

        <div className="relative inline-block">
          <button
            onClick={() => setDropdownOpen(!dropdownOpen)}
            className="inline-flex items-center gap-1.5 border px-3 py-1.5 rounded text-sm hover:bg-gray-50"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Download
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className={`h-3.5 w-3.5 transition-transform ${dropdownOpen ? "rotate-180" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {dropdownOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setDropdownOpen(false)} />
              <div className="absolute left-0 z-20 mt-1 w-40 bg-white border rounded-md shadow-lg py-1">
                {(["json", "md", "pdf", "csv", "all"] as DownloadFormat[]).map((fmt) => (
                  <button
                    key={fmt}
                    onClick={() => handleDownload(fmt)}
                    disabled={downloading !== null}
                    className="w-full text-left px-4 py-2 text-sm hover:bg-gray-100 disabled:opacity-50"
                  >
                    {downloading === fmt ? "Downloading..." : FORMAT_LABELS[fmt]}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {preview !== null && (
        <div
          data-testid="provenance-preview"
          className="border rounded-lg p-4 bg-gray-50 prose prose-sm max-w-none overflow-auto max-h-[500px]"
        >
          {"failed" in preview ? <p>Failed to load report preview.</p> : <ProvenancePreview model={preview.model} />}
        </div>
      )}
    </div>
  );
}
