"use client";

import { useMemo, useState } from "react";
import type { PerSampleInputSpec, SamplesheetPrefill } from "@/lib/types";

/** What the grid needs to identify a row. Narrower than SampleBrief on purpose:
 *  the grid identifies and orients, it does not render a sample record. */
export interface GridSample {
  id: number;
  external_id: string | null;
  organism?: string | null;
  tissue_type?: string | null;
}

export type PerSampleValues = Record<string, Record<string, string>>;

interface Props {
  /** The columns bioAF may not fill, from the preflight. Same computation as
   *  the block, so the grid cannot ask for something different from what the
   *  refusal reports. */
  specs: PerSampleInputSpec[];
  samples: GridSample[];
  values: PerSampleValues;
  onChange: (values: PerSampleValues) => void;
  /** A design saved earlier, offered rather than applied. */
  prefill: SamplesheetPrefill | null;
}

/** Column headings a pasted block may use for the sample's own name. */
const IDENTIFIER_HEADINGS = new Set(["sample", "sample_id", "sample id", "sample name", "external_id", "id"]);

const SCOPE_WORDING: Record<string, string> = {
  experiment: "carried over from this experiment",
  project: "carried over from this project",
  organization: "carried over from your organisation",
};

interface PendingPositionalPaste {
  rows: string[][];
  columns: string[];
}

function splitClipboard(text: string): string[][] {
  return text
    .replace(/\r/g, "")
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => line.split("\t").map((cell) => cell.trim()));
}

/**
 * Collects the values a pipeline demands per sample and bioAF may not guess:
 * mag's `group` decides co-assembly, rnasplice's `condition` defines the
 * differential contrast. Guessing either produces a run that completes green
 * and is scientifically wrong.
 *
 * The load-bearing rule is that a value belongs to a SAMPLE ID, never to a row
 * position. A spreadsheet sorted differently from the grid, or carrying a header
 * the paste includes, would otherwise assign every value to the wrong sample.
 * So a pasted block is matched on its identifier column whenever it has one, and
 * row order is used only when a person explicitly asks for it.
 */
export function PerSampleValueGrid({ specs, samples, values, onChange, prefill }: Props) {
  const [pasteNote, setPasteNote] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingPositionalPaste | null>(null);
  const [prefillApplied, setPrefillApplied] = useState(false);

  const byExternalId = useMemo(() => {
    const index = new Map<string, GridSample>();
    for (const sample of samples) {
      if (sample.external_id) index.set(sample.external_id.trim().toLowerCase(), sample);
    }
    return index;
  }, [samples]);

  if (specs.length === 0) return null;

  const columnNames = specs.map((spec) => spec.name);

  function setValue(sampleId: number, column: string, value: string) {
    const next: PerSampleValues = { ...values };
    const row = { ...(next[String(sampleId)] ?? {}) };
    if (value) row[column] = value;
    else delete row[column];
    if (Object.keys(row).length > 0) next[String(sampleId)] = row;
    else delete next[String(sampleId)];
    onChange(next);
  }

  function fillDown(column: string) {
    const first = samples.find((s) => (values[String(s.id)] ?? {})[column]);
    if (!first) return;
    const value = values[String(first.id)][column];
    const next: PerSampleValues = { ...values };
    for (const sample of samples) {
      next[String(sample.id)] = { ...(next[String(sample.id)] ?? {}), [column]: value };
    }
    onChange(next);
  }

  /** Apply a block whose rows name their sample. */
  function applyByIdentifier(rows: string[][], identifierAt: number, columns: string[]) {
    const next: PerSampleValues = { ...values };
    const unmatched: string[] = [];
    const named = new Set<number>();

    for (const cells of rows) {
      const identifier = (cells[identifierAt] ?? "").trim();
      const sample = byExternalId.get(identifier.toLowerCase());
      if (!sample) {
        if (identifier) unmatched.push(identifier);
        continue;
      }
      named.add(sample.id);
      const row = { ...(next[String(sample.id)] ?? {}) };
      cells.forEach((cell, index) => {
        const column = columns[index];
        if (!column || index === identifierAt || !cell) return;
        row[column] = cell;
      });
      next[String(sample.id)] = row;
    }

    const missed = samples.filter((s) => !named.has(s.id)).map((s) => s.external_id || `sample ${s.id}`);
    const notes: string[] = [];
    if (unmatched.length > 0) notes.push(`No selected sample is named ${unmatched.join(", ")}.`);
    if (missed.length > 0) notes.push(`Not named by this paste: ${missed.join(", ")}.`);
    setPasteNote(notes.join(" ") || null);
    onChange(next);
  }

  function handlePaste(event: React.ClipboardEvent) {
    event.preventDefault();
    const text = event.clipboardData.getData("text/plain") ?? "";
    const rows = splitClipboard(text);
    if (rows.length === 0) return;

    setPending(null);

    // A header row lets the paste say which column is which, so a spreadsheet
    // holding extra columns still lands correctly.
    const header = rows[0].map((cell) => cell.toLowerCase());
    const hasHeader = header.some((cell) => IDENTIFIER_HEADINGS.has(cell) || columnNames.includes(cell));
    const body = hasHeader ? rows.slice(1) : rows;
    if (body.length === 0) return;

    let identifierAt = hasHeader ? header.findIndex((cell) => IDENTIFIER_HEADINGS.has(cell)) : -1;
    if (identifierAt < 0) {
      // No header naming it: look for a column whose values are sample names.
      const width = Math.max(...body.map((cells) => cells.length));
      for (let index = 0; index < width; index += 1) {
        const matches = body.filter((cells) => byExternalId.has((cells[index] ?? "").trim().toLowerCase())).length;
        if (matches > 0) {
          identifierAt = index;
          break;
        }
      }
    }

    const columns = hasHeader
      ? rows[0].map((cell) => (columnNames.includes(cell.toLowerCase()) ? cell.toLowerCase() : ""))
      : [];

    if (identifierAt < 0) {
      // Never guess. Row order is a real answer for a block copied straight out
      // of the grid's own order, but it is the answer that silently mis-assigns
      // every value when it is wrong, so a person has to choose it.
      setPending({ rows: body, columns: columnNames });
      setPasteNote(null);
      return;
    }

    const resolved =
      columns.length > 0 && columns.some(Boolean)
        ? columns
        : // Unlabelled columns fall in the grid's own order, with the identifier
          // column skipped.
          body[0].map((_cell, index) => (index === identifierAt ? "" : columnNames[index > identifierAt ? index - 1 : index] ?? ""));

    applyByIdentifier(body, identifierAt, resolved);
  }

  function applyPositional() {
    if (!pending) return;
    const next: PerSampleValues = { ...values };
    pending.rows.forEach((cells, rowIndex) => {
      const sample = samples[rowIndex];
      if (!sample) return;
      const row = { ...(next[String(sample.id)] ?? {}) };
      cells.forEach((cell, index) => {
        const column = pending.columns[index];
        if (column && cell) row[column] = cell;
      });
      next[String(sample.id)] = row;
    });
    setPending(null);
    setPasteNote(null);
    onChange(next);
  }

  function useCarriedOverValues() {
    if (!prefill) return;
    const next: PerSampleValues = { ...values };
    for (const sample of samples) {
      const carried = prefill.values[String(sample.id)];
      if (carried) next[String(sample.id)] = { ...carried, ...(next[String(sample.id)] ?? {}) };
    }
    setPrefillApplied(true);
    onChange(next);
  }

  const carriedCount = prefill ? Object.keys(prefill.values).length : 0;
  const uncovered = prefill?.samples_without_values ?? [];
  const uncoveredInThisRun = samples.filter((s) => uncovered.includes(s.id));

  return (
    <div className="mb-6">
      <div className="mb-3">
        <h3 className="text-sm font-medium text-gray-900">Values this pipeline needs for each sample</h3>
        <p className="text-xs text-gray-500 mt-0.5">
          bioAF does not guess these. A wrong grouping or contrast runs to completion and is still wrong.
        </p>
      </div>

      {prefill && prefill.scope && carriedCount > 0 && (
        <div className="mb-3 p-3 border border-blue-200 bg-blue-50 rounded text-xs text-gray-700">
          <p>
            A saved design is available, {SCOPE_WORDING[prefill.scope] ?? `carried over from ${prefill.scope} scope`}.
          </p>
          {uncoveredInThisRun.length > 0 && (
            <p className="mt-1 font-medium text-gray-900">
              {uncoveredInThisRun.length} sample{uncoveredInThisRun.length === 1 ? " has" : "s have"} been added since
              this design was set. Review {columnNames.join(", ")} before launching.
            </p>
          )}
          {!prefillApplied && (
            <button
              type="button"
              onClick={useCarriedOverValues}
              className="mt-2 border border-blue-300 bg-white px-3 py-1 rounded text-xs hover:bg-blue-100"
            >
              Use carried-over values
            </button>
          )}
        </div>
      )}

      <textarea
        aria-label="Paste sample values"
        onPaste={handlePaste}
        readOnly
        value=""
        rows={1}
        placeholder="Paste a block from Excel or Sheets here. Include a sample column so each value lands on its own sample."
        className="w-full border rounded-md px-3 py-2 text-xs mb-2"
      />

      {pasteNote && (
        <p className="mb-2 text-xs text-yellow-700 bg-yellow-50 border border-yellow-200 rounded p-2" role="status">
          {pasteNote}
        </p>
      )}

      {pending && (
        <div className="mb-2 text-xs text-yellow-800 bg-yellow-50 border border-yellow-200 rounded p-2" role="status">
          <p>
            That block carries no sample identifier, so bioAF cannot tell which value belongs to which sample. Applying
            it in row order assigns values by position, which is wrong if the two orders differ.
          </p>
          <button
            type="button"
            onClick={applyPositional}
            className="mt-2 border border-yellow-300 bg-white px-3 py-1 rounded text-xs hover:bg-yellow-100"
          >
            Apply in row order
          </button>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Sample</th>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Organism</th>
              <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Tissue</th>
              {specs.map((spec) => (
                <th key={spec.name} scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500">
                  <div className="flex items-center gap-2">
                    <span className="uppercase">{spec.name.replace(/_/g, " ")}</span>
                    <button
                      type="button"
                      onClick={() => fillDown(spec.name)}
                      className="text-[10px] normal-case border px-1.5 py-0.5 rounded hover:bg-gray-100"
                    >
                      Fill {spec.name} down
                    </button>
                  </div>
                  {spec.description && (
                    <p className="mt-0.5 font-normal normal-case text-gray-500 max-w-xs">{spec.description}</p>
                  )}
                  {/* Shown for a column recorded on the SAMPLE: the pipeline's
                      list is what it can ingest, not what biology exists, so it
                      informs and never fences. */}
                  {!spec.constrained && spec.allowed_values.length > 0 && (
                    <p className="mt-0.5 font-normal normal-case text-gray-500">
                      This pipeline accepts: {spec.allowed_values.join(", ")}
                    </p>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {samples.map((sample) => (
              <tr key={sample.id}>
                <td className="px-3 py-2 text-sm">{sample.external_id || `#${sample.id}`}</td>
                <td className="px-3 py-2 text-sm text-gray-500">{sample.organism || ""}</td>
                <td className="px-3 py-2 text-sm text-gray-500">{sample.tissue_type || ""}</td>
                {specs.map((spec) => {
                  const label = `${spec.name} for ${sample.external_id || `#${sample.id}`}`;
                  const current = (values[String(sample.id)] ?? {})[spec.name] ?? "";
                  return (
                    <td key={spec.name} className="px-3 py-2">
                      {spec.constrained ? (
                        <select
                          aria-label={label}
                          value={current}
                          onChange={(e) => setValue(sample.id, spec.name, e.target.value)}
                          className="w-full border rounded-md px-2 py-1 text-sm"
                        >
                          <option value="">Choose...</option>
                          {spec.allowed_values.map((allowed) => (
                            <option key={allowed} value={allowed}>
                              {allowed}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          aria-label={label}
                          value={current}
                          onChange={(e) => setValue(sample.id, spec.name, e.target.value)}
                          className="w-full border rounded-md px-2 py-1 text-sm"
                        />
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
