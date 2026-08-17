"use client";

import { useState } from "react";
import type { RunSamplesheetDesign } from "@/lib/types";

interface Props {
  /** The exact sheet handed to Nextflow, as it was handed over. Null for runs
   *  launched before the snapshot existed; nothing is reconstructed, because a
   *  reconstruction reads today's samples and files rather than the run's. */
  csv: string | null;
  /** The stated design behind it, stamped with who set each value. */
  design: RunSamplesheetDesign | null;
  samples: { id: number; external_id: string | null }[];
}

/** Split one CSV line, keeping a quoted field whole. A storage URI or a free
 *  text note can carry a comma, and splitting on every comma would shift every
 *  column after it. */
function splitCsvLine(line: string): string[] {
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') {
      if (quoted && line[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === "," && !quoted) {
      cells.push(cell);
      cell = "";
    } else {
      cell += char;
    }
  }
  cells.push(cell);
  return cells;
}

/**
 * What this run was given, kept rather than re-derived (design section 10).
 *
 * Two things a result has to be defensible about later: which file went into
 * which column, and who said so. A mapping edited afterwards must not rewrite
 * the history of a run that already used it, so this reads the run's own
 * snapshot and never the current mapping.
 */
export function RunSamplesheetProvenance({ csv, design, samples }: Props) {
  const [shown, setShown] = useState(true);

  if (!csv) return null;

  const lines = csv.split("\n").filter((line) => line.trim() !== "");
  const columns = lines.length > 0 ? splitCsvLine(lines[0]) : [];
  const rows = lines.slice(1).map(splitCsvLine);

  const nameById = new Map(samples.map((s) => [String(s.id), s.external_id || `sample ${s.id}`]));
  const statedValues = Object.entries(design?.values ?? {});

  return (
    <div className="mb-6">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h3 className="text-sm font-medium text-gray-900">The samplesheet this run was given</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            Kept as submitted. It is a record of what ran, not a sheet rebuilt from today&apos;s data.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShown((open) => !open)}
            className="border px-3 py-1 rounded text-xs hover:bg-gray-100"
          >
            {shown ? "Hide samplesheet" : "Show samplesheet"}
          </button>
          <a
            href={`data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`}
            download="samplesheet.csv"
            className="border px-3 py-1 rounded text-xs hover:bg-gray-100"
          >
            Download CSV
          </a>
        </div>
      </div>

      {shown && (
        <>
          <div className="overflow-x-auto border rounded">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  {columns.map((column) => (
                    <th
                      key={column}
                      scope="col"
                      className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase"
                    >
                      {column.replace(/_/g, " ")}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {rows.map((cells, rowIndex) => (
                  <tr key={rowIndex}>
                    {columns.map((column, columnIndex) => (
                      <td key={column} className="px-3 py-2 text-sm font-mono break-all">
                        {cells[columnIndex] ?? ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {design && (
            <div className="mt-4">
              <h4 className="text-sm font-medium text-gray-900 mb-1">Values stated for this run</h4>
              {statedValues.length === 0 ? (
                <p className="text-xs text-gray-500">
                  No per-sample values were stated for this run. Every column came from the samples themselves.
                </p>
              ) : (
                <div className="overflow-x-auto border rounded">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Sample</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Column</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Value</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Stated by</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">When</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {statedValues.flatMap(([sampleId, byColumn]) =>
                        Object.entries(byColumn).map(([column, stamp]) => (
                          <tr key={`${sampleId}-${column}`}>
                            <td className="px-3 py-2 text-sm">{nameById.get(sampleId) ?? `sample ${sampleId}`}</td>
                            <td className="px-3 py-2 text-sm">{column.replace(/_/g, " ")}</td>
                            <td className="px-3 py-2 text-sm font-mono">{stamp.value}</td>
                            <td className="px-3 py-2 text-sm">{stamp.set_by ?? "Unknown"}</td>
                            <td className="px-3 py-2 text-sm text-gray-500">
                              {stamp.set_at ? new Date(stamp.set_at).toLocaleString() : ""}
                            </td>
                          </tr>
                        )),
                      )}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
