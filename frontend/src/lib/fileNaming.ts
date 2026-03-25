/**
 * Client-side filename suggestion logic.
 * Mirrors the Python CodeService.suggest_filename algorithm.
 *
 * Pattern: {ProjectCode}_{ExperimentCode}_{SampleID}_{DataType}_{YYYYMMDD}.ext
 * Segments are omitted when the corresponding value is null/undefined.
 */

const EXT_DATA_TYPES: [string, string][] = [
  [".fastq.gz", "FQ"],
  [".fq.gz", "FQ"],
  [".fastq", "FQ"],
  [".fq", "FQ"],
  [".bam", "BAM"],
  [".bai", "BAI"],
  [".h5ad", "counts"],
  [".loom", "counts"],
  [".csv", "data"],
  [".tsv", "data"],
  [".txt", "data"],
  [".pdf", "report"],
  [".html", "report"],
  [".png", "plot"],
  [".jpg", "plot"],
  [".jpeg", "plot"],
  [".svg", "plot"],
];

/** Split filename into [stem, ext], handling double extensions like .fastq.gz */
export function splitExtension(filename: string): [string, string] {
  const lower = filename.toLowerCase();
  for (const [dext] of EXT_DATA_TYPES) {
    if (lower.endsWith(dext)) {
      return [filename.slice(0, -dext.length), dext];
    }
  }
  const dot = filename.lastIndexOf(".");
  if (dot > 0) {
    return [filename.slice(0, dot), filename.slice(dot)];
  }
  return [filename, ""];
}

/** Infer a data_type label from the filename extension. */
export function inferDataType(filename: string): string | null {
  const lower = filename.toLowerCase();
  for (const [ext, label] of EXT_DATA_TYPES) {
    if (lower.endsWith(ext)) return label;
  }
  return null;
}

export interface SuggestOptions {
  projectCode: string | null;
  experimentCode: string | null;
  sampleId: string | null;
  dataType?: string | null;
  dateStr: string;
}

/**
 * Suggest a standardised filename for upload.
 * Returns null when no association is provided (nothing to suggest).
 */
export function suggestFilename(original: string, opts: SuggestOptions): string | null {
  const { projectCode, experimentCode, sampleId } = opts;
  if (!projectCode && !experimentCode && !sampleId) return null;

  const [, ext] = splitExtension(original);
  const effectiveType = opts.dataType ?? inferDataType(original);

  const segments: string[] = [];
  if (projectCode) segments.push(projectCode);
  if (experimentCode) segments.push(experimentCode);
  if (sampleId) segments.push(sampleId);
  if (effectiveType) segments.push(effectiveType);
  segments.push(opts.dateStr);

  return segments.join("_") + ext;
}

/** Return today's date as YYYYMMDD. */
export function todayDateStr(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}
