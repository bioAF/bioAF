"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";

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

function simpleMarkdownToHtml(md: string): string {
  return md
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>\n?)+/g, (match) => `<ul>${match}</ul>`)
    .replace(/\n{2,}/g, "<br/><br/>")
    .replace(/\n/g, "<br/>");
}

export function ProvenanceReportPanel({ entityType, entityId, entityName }: ProvenanceReportPanelProps) {
  const { canAccess, loading: permLoading } = usePermissions();
  const [preview, setPreview] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
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
      const lines: string[] = [];
      lines.push(`# Provenance Report: ${entityName}`);
      lines.push("");
      lines.push(`**Type:** ${report.report_type}`);
      lines.push(`**Schema Version:** ${report.schema_version}`);
      lines.push(`**Generated:** ${report.generated_at}`);
      lines.push("");

      const entity = report.entity as Record<string, unknown> | undefined;
      if (entity) {
        // Separate scalar fields from nested collections
        const scalarLines: string[] = [];
        const collectionLines: string[] = [];

        for (const [key, val] of Object.entries(entity)) {
          if (val == null || val === "") continue;
          // Skip source -- rendered separately below
          if (key === "source") continue;

          if (Array.isArray(val)) {
            const label = key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
            collectionLines.push(`- **${label}:** ${val.length} item${val.length !== 1 ? "s" : ""}`);
          } else if (typeof val === "object") {
            const obj = val as Record<string, unknown>;
            if (obj.email) {
              scalarLines.push(`- **${key}:** ${obj.name || obj.email}`);
            } else if (obj.raw !== undefined || obj.results !== undefined) {
              const rawCount = Array.isArray(obj.raw) ? obj.raw.length : 0;
              const resCount = Array.isArray(obj.results) ? obj.results.length : 0;
              collectionLines.push(`- **Files:** ${rawCount} raw, ${resCount} results`);
            } else if (obj.files !== undefined || obj.samples !== undefined) {
              for (const [subKey, subVal] of Object.entries(obj)) {
                if (Array.isArray(subVal)) {
                  const label = subKey.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
                  collectionLines.push(`- **${label}:** ${subVal.length}`);
                }
              }
            }
          } else {
            scalarLines.push(`- **${key}:** ${String(val)}`);
          }
        }

        if (scalarLines.length > 0) {
          lines.push("## Entity Details");
          lines.push(...scalarLines);
          lines.push("");
        }

        // Render source section for artifacts
        const source = entity.source as Record<string, unknown> | undefined;
        if (source) {
          const sourceType = source.type as string;
          lines.push("## Source");
          lines.push(`- **Source Type:** ${sourceType}`);

          const ns = source.notebook_session as Record<string, unknown> | undefined;
          if (ns) {
            lines.push(`- **Session ID:** ${ns.id}`);
            lines.push(`- **Session Type:** ${ns.session_type}`);
            lines.push(`- **Status:** ${ns.status}`);
            lines.push(`- **Resources:** ${ns.cpu_cores} CPU, ${ns.memory_gb} GB`);
            if (ns.started_at) lines.push(`- **Started:** ${ns.started_at}`);
            if (ns.stopped_at) lines.push(`- **Stopped:** ${ns.stopped_at}`);
            if (ns.git_branch_name) lines.push(`- **Git Branch:** ${ns.git_branch_name}`);
            if (ns.git_commit_hash) lines.push(`- **Git Commit:** ${ns.git_commit_hash}`);

            const env = ns.environment as Record<string, unknown> | undefined;
            if (env) {
              lines.push("");
              lines.push("### Environment");
              lines.push(`- **Name:** ${env.environment_name}`);
              lines.push(`- **Version:** v${env.version_number}.${env.build_number}`);
              lines.push(`- **Format:** ${env.definition_format}`);
              if (env.image_uri) lines.push(`- **Image:** ${env.image_uri}`);
            }

            const inputFiles = ns.input_files as Array<Record<string, unknown>> | undefined;
            if (inputFiles && inputFiles.length > 0) {
              lines.push("");
              lines.push(`### Input Files (${inputFiles.length})`);
              for (const f of inputFiles) {
                lines.push(`- ${f.filename} (${f.file_type})`);
              }
            }
          }

          const pr = source.pipeline_run as Record<string, unknown> | undefined;
          if (pr) {
            lines.push(`- **Pipeline:** ${pr.pipeline_name}`);
            if (pr.pipeline_version) lines.push(`- **Version:** ${pr.pipeline_version}`);
            lines.push(`- **Run ID:** ${pr.id}`);
            lines.push(`- **Status:** ${pr.status}`);
          }

          lines.push("");
        }

        if (collectionLines.length > 0) {
          lines.push("## Related Data");
          lines.push(...collectionLines);
          lines.push("");
        }
      }

      const auditTrail = report.audit_trail as unknown[] | undefined;
      if (Array.isArray(auditTrail) && auditTrail.length > 0) {
        lines.push(`## Audit Trail`);
        lines.push(`${auditTrail.length} entries`);
      }

      setPreview(lines.join("\n"));
    } catch (err) {
      console.error("Failed to load provenance preview:", err);
      setPreview("Failed to load report preview.");
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
          className="border rounded-lg p-4 bg-gray-50 prose prose-sm max-w-none overflow-auto max-h-[500px]"
          dangerouslySetInnerHTML={{ __html: simpleMarkdownToHtml(preview) }}
        />
      )}
    </div>
  );
}
