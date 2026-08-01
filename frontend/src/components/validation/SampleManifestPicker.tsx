"use client";

import { useState } from "react";

// One recognizable sample from the study's public deposit metadata (backend SampleManifestResponse).
export interface ManifestSample {
  experiment_accession: string;
  run_accession: string;
  sample_accession: string;
  title: string;
  condition: string;
}

export type Arm = "test" | "reference" | "exclude";

// The stored pick is the stable public experiment accession (resolved to the real Sample.external_id
// post-fetch); fall back to run/sample if a deposit lacks one.
export function sampleId(s: ManifestSample): string {
  return s.experiment_accession || s.run_accession || s.sample_accession || "";
}

const ARMS: Array<{ value: Arm; label: string }> = [
  { value: "test", label: "Test" },
  { value: "reference", label: "Reference" },
  { value: "exclude", label: "Exclude" },
];

/**
 * The Level-3 sample picker: the scientist assigns the study's real samples to the Test / Reference
 * arms by RECOGNIZING them (title + condition), never by typing accession tokens. Presentational; the
 * parent (Level3Gate) owns the assignment state, pre-grouping, and save. Manual-add covers a sample
 * the metadata missed.
 */
export function SampleManifestPicker({
  samples,
  manualIds,
  assignments,
  subjects,
  onAssign,
  onSubject,
  onManualAdd,
}: {
  samples: ManifestSample[];
  manualIds: string[];
  assignments: Record<string, Arm>;
  subjects: Record<string, string>;
  onAssign: (id: string, arm: Arm) => void;
  onSubject: (id: string, subject: string) => void;
  onManualAdd: (id: string) => void;
}) {
  const [manual, setManual] = useState("");

  const rows: ManifestSample[] = [
    ...samples,
    ...manualIds.map((id) => ({
      experiment_accession: id,
      run_accession: "",
      sample_accession: "",
      title: id,
      condition: "Manually added",
    })),
  ];

  const input = "w-full rounded border border-gray-300 px-2 py-1 text-sm";

  function addManual() {
    const id = manual.trim();
    if (!id) return;
    onManualAdd(id);
    setManual("");
  }

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        Assign samples to the test and reference arms
      </p>
      <p className="text-[11px] leading-snug text-gray-500">
        Pick samples by recognizing them. We pre-group by the reported condition where we can; confirm or
        move any sample. Excluded samples are left out of the differential run.
      </p>

      <ul className="divide-y divide-gray-200 rounded border border-gray-200 bg-white">
        {rows.map((s) => {
          const id = sampleId(s);
          const arm = assignments[id] ?? "exclude";
          return (
            <li key={id} className="flex flex-wrap items-center gap-2 px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-gray-800">{s.title || id}</p>
                <p className="truncate text-xs text-gray-500">
                  {s.condition && <span>{s.condition}</span>}
                  {s.condition && id && <span className="text-gray-400"> &middot; </span>}
                  {id && <span className="font-mono text-gray-400">{id}</span>}
                </p>
              </div>
              <label className="text-xs text-gray-600">
                <span className="sr-only">Arm for {s.title || id}</span>
                <select
                  className={input}
                  aria-label={`Arm for ${s.title || id}`}
                  value={arm}
                  onChange={(e) => onAssign(id, e.target.value as Arm)}
                >
                  {ARMS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-gray-600">
                <span className="sr-only">Subject for {s.title || id}</span>
                <input
                  className={`${input} w-24`}
                  aria-label={`Subject for ${s.title || id}`}
                  placeholder="subject"
                  value={subjects[id] ?? ""}
                  onChange={(e) => onSubject(id, e.target.value)}
                  disabled={arm === "exclude"}
                />
              </label>
            </li>
          );
        })}
      </ul>

      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-gray-600">
          Add a sample the list missed (accession)
          <input
            className={input}
            aria-label="Add a sample id"
            value={manual}
            onChange={(e) => setManual(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addManual();
              }
            }}
            placeholder="SRX..."
          />
        </label>
        <button
          type="button"
          className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
          onClick={addManual}
          disabled={manual.trim() === ""}
        >
          Add sample
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-snug text-gray-500">
        Optional per-sample <span className="font-medium">subject</span>: give the same label to the paired
        samples in both arms to model <code>~ subject + condition</code> (matched pairs).
      </p>
    </div>
  );
}
