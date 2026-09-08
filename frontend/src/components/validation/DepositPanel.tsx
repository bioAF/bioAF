"use client";

import { useState } from "react";

/**
 * plan_7 step 15: the rest of the C1 gate, which step 10 only half built.
 *
 * The route modal shipped in `7d36acad`; the deposit inventory, the assisted picker, the inspection
 * numbers and the metadata association table did not. Without the picker, `assisted` is not a
 * working mode at all: the driver lists the deposit, stores it, and waits for a person who has no
 * control to answer with.
 *
 * Follows `SampleManifestPicker`, which already does recognisable-row picking with arms. This is
 * the same interaction over a different list.
 *
 * The MEASUREMENT overrules the model's guess about what the matrix holds, and both are shown. A
 * file named `counts.tsv` holding floats that sum to 1e6 per column is a CPM table whatever the
 * depositor called it, and a disagreement between the two is worth seeing rather than resolving
 * silently.
 */

export interface DepositEntry {
  filename: string;
  url: string;
  classification: string;
  level: string;
  gsm: string | null;
  size_bytes: number | null;
  deposited_type: string | null;
}

export interface DepositSelection {
  primary_matrix: string | null;
  matrix_files: string[];
  metadata_file: string | null;
  value_type: string;
  reason: string;
  confidence: number;
  declined: boolean;
  decided_by?: string;
  model?: string | null;
}

export interface DepositInspection {
  value_type_observed: string;
  value_type_claimed?: string | null;
  value_type_disagrees?: boolean;
  n_rows: number;
  n_columns: number;
  columns: string[];
  usable: boolean;
  unusable_reason: string | null;
  library_size_ratio: number | null;
}

export interface DepositAssociation {
  column: string;
  condition: string | null;
  source: string;
  confidence: number;
  reason?: string;
}

export interface DepositEvidence {
  deposit_inventory?: {
    accession: string;
    source: string | null;
    listed_at: string;
    entries: DepositEntry[];
    triplets: unknown[];
  } | null;
  deposit_selection?: DepositSelection | null;
  deposit_inspection?: DepositInspection | null;
  deposit_metadata_association?: DepositAssociation[] | null;
  deposit_failed?: { reason: string; at: string } | null;
}

// What can be a reproduction input. Everything else is deposited and not a candidate: a raw archive
// is a tarball of reads, a coverage track carries no per-feature values, and the paper's own result
// table is the ANSWER rather than something to recompute from.
const SELECTABLE = new Set(["matrix_counts", "matrix_normalized", "peaks", "barcodes", "features"]);

const SOURCE_LABEL: Record<string, string> = {
  metadata_file: "metadata file",
  series_matrix: "GEO series matrix",
  column_name: "column name",
  unresolved: "unresolved",
};

function humanSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "size unknown";
  for (const [unit, div] of [
    ["GB", 1024 ** 3],
    ["MB", 1024 ** 2],
    ["KB", 1024],
  ] as const) {
    if (bytes >= div) return `${(bytes / div).toFixed(1)} ${unit}`;
  }
  return `${bytes} bytes`;
}

export function DepositPanel({
  evidence,
  canPick,
  onPick,
}: {
  evidence: DepositEvidence;
  canPick: boolean;
  onPick: (selection: DepositSelection) => void;
}) {
  const inventory = evidence.deposit_inventory;
  const selection = evidence.deposit_selection;
  const inspection = evidence.deposit_inspection;
  const association = evidence.deposit_metadata_association;
  const failed = evidence.deposit_failed;

  const entries = inventory?.entries ?? [];
  const candidates = entries.filter((e) => SELECTABLE.has(e.classification));
  const metadataFiles = entries.filter((e) => e.classification === "metadata");

  const [matrix, setMatrix] = useState("");
  const [metadata, setMetadata] = useState("");

  if (!inventory && !selection && !inspection && !failed) return null;

  const series = entries.filter((e) => e.level === "series");
  const perSample = entries.filter((e) => e.level === "sample");

  return (
    <div className="space-y-3 text-sm">
      {failed && (
        <p className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">{failed.reason}</p>
      )}

      {selection && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Chosen to reproduce from</p>
          <p className="text-gray-800">
            {selection.primary_matrix || "nothing was chosen"}
            {selection.matrix_files.length > 1 && (
              <span className="ml-1 text-xs text-gray-500">
                (and {selection.matrix_files.length - 1} more per-sample file
                {selection.matrix_files.length > 2 ? "s" : ""})
              </span>
            )}
          </p>
          <p className="text-xs text-gray-500">
            {selection.reason}
            {selection.decided_by === "model" && (
              <>
                {" "}
                <span className="tabular-nums">{selection.confidence.toFixed(2)}</span>{" "}
                <span className="font-mono">{selection.model}</span>
              </>
            )}
            {selection.decided_by === "human" && " (chosen by a person)"}
          </p>
        </div>
      )}

      {canPick && !selection && candidates.length > 0 && (
        <div className="space-y-2 rounded border border-gray-200 p-3">
          <p className="text-xs text-gray-600">
            Choose which deposited file to reproduce from. Only files carrying per-feature values are
            offered; a coverage track or an archive of raw reads cannot be used.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-gray-600">
              <span className="block">Matrix to reproduce from</span>
              <select
                className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
                value={matrix}
                onChange={(e) => setMatrix(e.target.value)}
              >
                <option value="">Select a file</option>
                {candidates.map((e) => (
                  <option key={e.filename} value={e.filename}>
                    {e.filename} ({e.classification}, {humanSize(e.size_bytes)})
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-gray-600">
              <span className="block">Sample metadata (optional)</span>
              <select
                className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
                value={metadata}
                onChange={(e) => setMetadata(e.target.value)}
              >
                <option value="">None</option>
                {metadataFiles.map((e) => (
                  <option key={e.filename} value={e.filename}>
                    {e.filename}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              disabled={!matrix}
              onClick={() => {
                const chosen = candidates.find((c) => c.filename === matrix);
                // A per-sample pick is a GROUP: one peak file is a column, not a matrix, and a
                // differential test needs both arms. Same rule the model's pick follows.
                const files =
                  chosen && chosen.level === "sample"
                    ? candidates
                        .filter((c) => c.level === "sample" && c.classification === chosen.classification)
                        .map((c) => c.filename)
                        .sort()
                    : [matrix];
                onPick({
                  primary_matrix: matrix,
                  matrix_files: files,
                  metadata_file: metadata || null,
                  // Deliberately not guessed here. Step 6 MEASURES what the values are and its
                  // answer overrules any claim, so asking a person is asking for a guess we ignore.
                  value_type: "unknown",
                  reason: "chosen at the approval gate",
                  confidence: 1.0,
                  declined: false,
                  decided_by: "human",
                  model: null,
                });
              }}
            >
              Use this file
            </button>
          </div>
        </div>
      )}

      {inventory && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Deposited files ({entries.length})
          </p>
          {series.length > 0 && <FileGroup title="Series level" entries={series} />}
          {perSample.length > 0 && <FileGroup title="Per sample" entries={perSample} />}
        </div>
      )}

      {inspection && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Measured before anything ran on it
          </p>
          <p className="text-xs text-gray-600">
            {inspection.n_rows.toLocaleString()} rows, {inspection.n_columns} sample column
            {inspection.n_columns === 1 ? "" : "s"}, values measured as{" "}
            <span className="font-mono">{inspection.value_type_observed}</span>
            {inspection.value_type_claimed && inspection.value_type_claimed !== inspection.value_type_observed && (
              <>
                {" "}
                (the filename suggested <span className="font-mono">{inspection.value_type_claimed}</span>; the
                measurement stands)
              </>
            )}
            .
          </p>
          {inspection.unusable_reason && (
            <p className="text-xs text-amber-800">{inspection.unusable_reason}</p>
          )}
        </div>
      )}

      {association && association.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            What each column is, and how we know
          </p>
          <table className="mt-1 min-w-full text-xs">
            <tbody className="divide-y divide-gray-100">
              {association.map((row) => (
                <tr key={row.column}>
                  <td className="py-1 pr-3 font-mono text-gray-800">{row.column}</td>
                  <td className="py-1 pr-3 text-gray-700">{row.condition || "unresolved"}</td>
                  <td className="py-1 pr-3 text-gray-500">{SOURCE_LABEL[row.source] ?? row.source}</td>
                  <td className="py-1 tabular-nums text-gray-500">{row.confidence.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function FileGroup({ title, entries }: { title: string; entries: DepositEntry[] }) {
  return (
    <div className="mt-1">
      <p className="text-xs text-gray-500">{title}</p>
      <ul className="divide-y divide-gray-100 border-y border-gray-100">
        {entries.map((e) => (
          <li key={e.filename} className="flex flex-wrap items-baseline gap-x-2 py-1 text-xs">
            <span className="font-mono text-gray-800">{e.filename}</span>
            <span className="text-gray-500">{e.classification}</span>
            <span className="text-gray-500">{humanSize(e.size_bytes)}</span>
            {e.gsm && <span className="text-gray-500">{e.gsm}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
