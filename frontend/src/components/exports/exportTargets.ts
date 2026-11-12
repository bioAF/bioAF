/**
 * What differs between the experiment and project data exports: the entity's endpoints, the
 * archive's fallback name, and the shape of its size estimate. Everything else (options, delivery,
 * lifecycle, presentation) is shared by `useDataExport` and `DataExportDialog`.
 */

export interface ExportOptions {
  includeFastq: boolean;
  includeProvenance: boolean;
}

export const DEFAULT_EXPORT_OPTIONS: ExportOptions = { includeFastq: false, includeProvenance: true };

export interface ExportEstimate {
  total_bytes: number;
  breakdown: Record<string, number>;
}

export interface ExperimentEstimateRow {
  experiment_id: number;
  name: string;
  total_bytes: number;
}

export interface ProjectExportEstimate extends ExportEstimate {
  experiments: ExperimentEstimateRow[];
}

export interface ExportTarget<E extends ExportEstimate = ExportEstimate> {
  /** Identifies the entity; a different key starts the dialog from the defaults. */
  key: string;
  basePath: string;
  fallbackFilename: string;
  parseEstimate: (raw: unknown) => E | null;
}

export interface SignedLink {
  url: string;
  /** Null when the response gave no usable expiry; the view then says nothing about it. */
  expiresInHours: number | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSize(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function parseBaseEstimate(raw: unknown): ExportEstimate | null {
  if (!isRecord(raw) || !isSize(raw.total_bytes) || !isRecord(raw.breakdown)) return null;
  if (!Object.values(raw.breakdown).every(isSize)) return null;
  return { total_bytes: raw.total_bytes, breakdown: raw.breakdown as Record<string, number> };
}

function parseProjectEstimate(raw: unknown): ProjectExportEstimate | null {
  const base = parseBaseEstimate(raw);
  if (!base || !isRecord(raw) || !Array.isArray(raw.experiments)) return null;
  const rows = raw.experiments;
  const valid = rows.every(
    (row) =>
      isRecord(row) && typeof row.experiment_id === "number" && typeof row.name === "string" && isSize(row.total_bytes),
  );
  return valid ? { ...base, experiments: rows as ExperimentEstimateRow[] } : null;
}

export function experimentExportTarget(experimentId: number): ExportTarget<ExportEstimate> {
  return {
    key: `experiment:${experimentId}`,
    basePath: `/api/experiments/${experimentId}/export`,
    fallbackFilename: `experiment_${experimentId}_export.zip`,
    parseEstimate: parseBaseEstimate,
  };
}

export function projectExportTarget(projectId: number): ExportTarget<ProjectExportEstimate> {
  return {
    key: `project:${projectId}`,
    basePath: `/api/projects/${projectId}/export`,
    fallbackFilename: `project_${projectId}_export.zip`,
    parseEstimate: parseProjectEstimate,
  };
}

/**
 * A signed link the browser may open: an absolute HTTP(S) URL from any host (the storage provider
 * decides where), never another scheme. The expiry is kept only when it is a positive number.
 */
export function parseSignedLink(raw: unknown): SignedLink | null {
  if (!isRecord(raw) || typeof raw.signed_url !== "string") return null;
  let url: URL;
  try {
    url = new URL(raw.signed_url);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  const hours = raw.expires_in_hours;
  const expiresInHours = typeof hours === "number" && Number.isFinite(hours) && hours > 0 ? hours : null;
  return { url: raw.signed_url, expiresInHours };
}

/** Exports above this size are better fetched through a link than held open in the tab. */
export const LARGE_EXPORT_BYTES = 1_073_741_824;
