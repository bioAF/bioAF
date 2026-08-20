"use client";

import type { DeclaredColumn, BindingSource } from "@/lib/types";

/** The standard sheet, and what the editor opens on when nothing is declared.
 *  A scientist who opens this and saves it untouched must get the file they got
 *  before, or the editor is a way to break a launch that already works. */
export const STANDARD_COLUMNS: DeclaredColumn[] = [
  { name: "sample", type: "string", required: true, binding: { source: "sample_field", key: "external_id" } },
  { name: "fastq_1", type: "file", required: false, binding: { source: "read", key: "1" } },
  { name: "fastq_2", type: "file", required: false, binding: { source: "read", key: "2" } },
];

/** Where a column's value may come from, in the words a scientist would use.
 *  The empty source is not an omission: a co-assembly grouping or a
 *  differential contrast is a design decision no binding can derive, so the
 *  honest answer is to ask for it per sample. */
const SOURCES: { value: BindingSource | ""; label: string }[] = [
  { value: "", label: "Asked for each sample" },
  { value: "read", label: "The sample's reads" },
  { value: "sample_field", label: "A field on the sample" },
  { value: "file_type", label: "One of the sample's files" },
  { value: "custom_field", label: "A custom field on the sample" },
  { value: "literal", label: "The same value in every row" },
];

/** Sample fields a column may be bound to. Mirrors the backend allowlist: open
 *  reflection would let a samplesheet column read an internal id. */
const SAMPLE_FIELDS = [
  "external_id",
  "donor_source",
  "organism",
  "tissue_type",
  "treatment_condition",
  "chemistry_version",
  "sample_batch_code",
  "sequencing_batch_code",
  "molecule_type",
  "library_prep_method",
  "library_layout",
  "assay",
  "sex",
];

function keyProblem(column: DeclaredColumn): string | null {
  const source = column.binding?.source;
  const key = (column.binding?.key || "").trim();
  if (!source || key) return null;
  if (source === "file_type") return "This column needs to say which file it takes.";
  if (source === "custom_field") return "This column needs to say which custom field it takes.";
  if (source === "literal") return "This column needs the value to put in every row.";
  if (source === "read") return "This column needs to say whether it holds read 1 or read 2.";
  if (source === "sample_field") return "This column needs to say which field on the sample it takes.";
  return null;
}

interface Props {
  columns: DeclaredColumn[];
  /** The file types this experiment's samples actually carry, so a binding is
   *  chosen from what exists rather than typed from memory. */
  fileTypes: string[];
  customFields: string[];
  onChange: (columns: DeclaredColumn[]) => void;
}

/**
 * Declaring a samplesheet for a pipeline that publishes none.
 *
 * Seventeen pipelines in the catalog ship no `schema_input.json`. bioAF emitted
 * a fixed `sample,fastq_1,fastq_2` header for all of them and ignored anything
 * the scientist stated, so nothing they could do reached the file. Here they say
 * what the columns are, and each column carries a BINDING saying where its value
 * comes from.
 *
 * The order shown is the order emitted. There is no published schema to fall
 * back on, so the scientist's own order is the only statement about this sheet's
 * shape that exists.
 */
export function SamplesheetColumnEditor({ columns, fileTypes, customFields, onChange }: Props) {
  const names = columns.map((c) => (c.name || "").trim());
  const duplicated = new Set(names.filter((name, at) => name && names.indexOf(name) !== at));

  function replace(at: number, column: DeclaredColumn) {
    onChange(columns.map((existing, index) => (index === at ? column : existing)));
  }

  function setSource(at: number, source: string) {
    const column = columns[at];
    if (!source) {
      const { binding: _dropped, ...rest } = column;
      replace(at, rest);
      return;
    }
    // The key is cleared whenever the source changes, because a key means
    // something different under each one: a file type is not a custom field
    // name, and carrying it across would silently bind to something that
    // happens to share a spelling.
    const key = column.binding?.source === source ? column.binding.key : "";
    replace(at, { ...column, binding: { source: source as BindingSource, key } });
  }

  function move(at: number, by: number) {
    const next = [...columns];
    const [moved] = next.splice(at, 1);
    next.splice(at + by, 0, moved);
    onChange(next);
  }

  return (
    <div className="mb-6 border rounded-md p-3">
      <h3 className="font-medium text-sm text-gray-700 mb-1">Samplesheet columns</h3>
      <p className="text-xs text-gray-600 mb-3">
        This pipeline does not publish a samplesheet contract, so bioAF cannot know what its columns
        are. Declare them here and say where each value comes from. They are emitted in this order.
      </p>

      {columns.length === 0 && (
        <div className="text-sm text-gray-600 mb-3">
          Nothing is declared, so bioAF will send its standard sheet:{" "}
          <code className="text-xs">sample,fastq_1,fastq_2</code>.
        </div>
      )}

      <div className="space-y-2">
        {columns.map((column, at) => {
          const problem = keyProblem(column);
          const label = column.name || `column ${at + 1}`;
          return (
            <div key={at} className="grid grid-cols-1 md:grid-cols-12 gap-2 items-start border-t pt-2">
              <div className="md:col-span-3">
                <label className="text-xs text-gray-500" htmlFor={`column-name-${at}`}>
                  Column name
                </label>
                <input
                  id={`column-name-${at}`}
                  type="text"
                  value={column.name}
                  onChange={(e) => replace(at, { ...column, name: e.target.value })}
                  className="w-full border rounded px-2 py-1 text-sm"
                />
                {!column.name.trim() && (
                  <p className="text-xs text-amber-700 mt-1">This column needs a name.</p>
                )}
                {column.name.trim() && duplicated.has(column.name.trim()) && (
                  <p className="text-xs text-amber-700 mt-1">
                    {column.name.trim()} is declared twice.
                  </p>
                )}
              </div>

              <div className="md:col-span-4">
                <label className="text-xs text-gray-500" htmlFor={`column-source-${at}`}>
                  {`Value for ${label} comes from`}
                </label>
                <select
                  id={`column-source-${at}`}
                  value={column.binding?.source ?? ""}
                  onChange={(e) => setSource(at, e.target.value)}
                  className="w-full border rounded px-2 py-1 text-sm"
                >
                  {SOURCES.map((source) => (
                    <option key={source.value} value={source.value}>
                      {source.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="md:col-span-3">
                <BindingKey
                  at={at}
                  label={label}
                  column={column}
                  fileTypes={fileTypes}
                  customFields={customFields}
                  onChange={(key) =>
                    replace(at, {
                      ...column,
                      binding: { source: column.binding!.source, key },
                    })
                  }
                />
                {problem && <p className="text-xs text-amber-700 mt-1">{problem}</p>}
              </div>

              <div className="md:col-span-2 flex items-end gap-1 pt-4">
                <label className="text-xs text-gray-600 flex items-center gap-1 mr-1">
                  <input
                    type="checkbox"
                    checked={Boolean(column.required)}
                    onChange={(e) => replace(at, { ...column, required: e.target.checked })}
                    aria-label={`${label} is required`}
                  />
                  Required
                </label>
                <button
                  type="button"
                  onClick={() => move(at, -1)}
                  disabled={at === 0}
                  aria-label={`Move ${label} up`}
                  className="border rounded px-2 text-sm disabled:opacity-40"
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={() => move(at, 1)}
                  disabled={at === columns.length - 1}
                  aria-label={`Move ${label} down`}
                  className="border rounded px-2 text-sm disabled:opacity-40"
                >
                  ↓
                </button>
                <button
                  type="button"
                  onClick={() => onChange(columns.filter((_, index) => index !== at))}
                  aria-label={`Remove ${label}`}
                  className="border rounded px-2 text-sm"
                >
                  ×
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex gap-2 mt-3">
        <button
          type="button"
          onClick={() => onChange([...columns, { name: "", type: "string", required: false }])}
          className="border px-3 py-1 rounded-md text-sm hover:bg-gray-100"
        >
          Add a column
        </button>
        {columns.length === 0 && (
          <button
            type="button"
            onClick={() => onChange(STANDARD_COLUMNS)}
            className="border px-3 py-1 rounded-md text-sm hover:bg-gray-100"
          >
            Start from the standard sheet
          </button>
        )}
      </div>
    </div>
  );
}

/** The second half of a binding: WHICH field, file type, custom field or value.
 *  Chosen from what the experiment actually holds wherever bioAF knows, because
 *  a typed-from-memory file type binds to nothing and the column then blocks the
 *  launch with no hint as to why. */
function BindingKey({
  at,
  label,
  column,
  fileTypes,
  customFields,
  onChange,
}: {
  at: number;
  label: string;
  column: DeclaredColumn;
  fileTypes: string[];
  customFields: string[];
  onChange: (key: string) => void;
}) {
  const source = column.binding?.source;
  const key = column.binding?.key ?? "";
  const id = `column-key-${at}`;

  if (!source) {
    return <p className="text-xs text-gray-500 pt-4">Asked for each sample in the grid below.</p>;
  }

  const options: Record<string, string[]> = {
    read: ["1", "2"],
    sample_field: SAMPLE_FIELDS,
    file_type: fileTypes,
    custom_field: customFields,
  };

  const labels: Record<string, string> = {
    read: `Which read for ${label}`,
    sample_field: `Which field for ${label}`,
    file_type: `Which file for ${label}`,
    custom_field: `Which custom field for ${label}`,
    literal: `Value for every row of ${label}`,
  };

  if (source === "literal") {
    return (
      <>
        <label className="text-xs text-gray-500" htmlFor={id}>
          {labels[source]}
        </label>
        <input
          id={id}
          type="text"
          value={key}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border rounded px-2 py-1 text-sm"
        />
      </>
    );
  }

  return (
    <>
      <label className="text-xs text-gray-500" htmlFor={id}>
        {labels[source]}
      </label>
      <select
        id={id}
        value={key}
        onChange={(e) => onChange(e.target.value)}
        className="w-full border rounded px-2 py-1 text-sm"
      >
        <option value="">--</option>
        {(options[source] || []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </>
  );
}
