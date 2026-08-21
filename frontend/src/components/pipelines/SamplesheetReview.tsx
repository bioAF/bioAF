"use client";

import { useState } from "react";
import type { SamplesheetPreview } from "@/lib/types";

interface Props {
  /** The sheet this run would submit, produced by the generator that feeds
   *  Nextflow rather than by a second code path. Null while it is unknown. */
  preview: SamplesheetPreview | null;
  /** A value corrected in place. Reported against the sample and column, so a
   *  correction can never land on a neighbouring row. */
  onCorrect: (sampleId: number, column: string, value: string) => void;
}

/**
 * The review every launch ends with (design section 6).
 *
 * bioAF resolves a file column by matching the schema's declared pattern, and a
 * regex match is not proof of the right file: a reference genome satisfies
 * funcscan's `fasta` pattern exactly as well as the scientist's assembly does.
 * Nothing else in the flow would catch that, which is why this is a step rather
 * than an option, and a table rather than a CSV dump.
 */
export function SamplesheetReview({ preview, onCorrect }: Props) {
  const [showRaw, setShowRaw] = useState(false);

  if (!preview || (preview.columns.length === 0 && !preview.csv)) return null;

  const omissions = preview.omissions ?? [];

  return (
    <div className="mb-6">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h3 className="text-sm font-medium text-gray-900">The samplesheet this run will submit</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            Change any value here. A correction applies to this run; saving it for next time is a separate step.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowRaw((shown) => !shown)}
            className="border px-3 py-1 rounded text-xs hover:bg-gray-100"
          >
            {showRaw ? "Hide raw CSV" : "Show raw CSV"}
          </button>
          <a
            href={`data:text/csv;charset=utf-8,${encodeURIComponent(preview.csv)}`}
            download="samplesheet.csv"
            className="border px-3 py-1 rounded text-xs hover:bg-gray-100"
          >
            Download CSV
          </a>
        </div>
      </div>

      {omissions.length > 0 && (
        <div
          role="note"
          className="mb-3 p-3 border border-yellow-200 bg-yellow-50 rounded text-xs text-gray-700"
        >
          <p className="font-medium text-gray-900 mb-1">Left out of the sheet</p>
          <ul className="space-y-1">
            {omissions.map((omission) => (
              <li key={`${omission.column}-${omission.sample_id}`}>
                <span className="font-medium">{omission.column.replace(/_/g, " ")}</span>
                {" for "}
                {omission.external_id || `sample ${omission.sample_id}`}
                {": this pipeline cannot express "}
                <span className="font-mono">{omission.value}</span>
                {omission.allowed_values.length > 0 && <>. It accepts: {omission.allowed_values.join(", ")}</>}
                . The sample record keeps its own value.
              </li>
            ))}
          </ul>
        </div>
      )}

      {preview.columns.length > 0 ? (
        <div className="overflow-x-auto border rounded">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {preview.columns.map((column) => (
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
              {preview.rows.map((row, rowIndex) => (
                <tr key={`${row.sample_id ?? "unmatched"}-${rowIndex}`}>
                  {preview.columns.map((column, columnIndex) => {
                    const value = row.values[columnIndex] ?? "";
                    // A row bioAF could not attribute to a sample is shown and
                    // not edited: a correction has to belong to a sample, and
                    // guessing which one is the failure this whole step exists
                    // to catch.
                    if (row.sample_id === null) {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-500 font-mono break-all">
                          {value}
                        </td>
                      );
                    }
                    const sampleId = row.sample_id;
                    return (
                      <td key={column} className="px-3 py-2">
                        <input
                          aria-label={`${column} for ${row.external_id || `sample ${sampleId}`}`}
                          value={value}
                          onChange={(e) => onCorrect(sampleId, column, e.target.value)}
                          className="w-full min-w-[8rem] border rounded-md px-2 py-1 text-sm font-mono"
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <pre className="border rounded p-3 text-xs overflow-x-auto whitespace-pre-wrap">{preview.csv}</pre>
      )}

      {showRaw && preview.columns.length > 0 && (
        <pre className="mt-2 border rounded p-3 text-xs overflow-x-auto whitespace-pre-wrap">{preview.csv}</pre>
      )}
    </div>
  );
}
