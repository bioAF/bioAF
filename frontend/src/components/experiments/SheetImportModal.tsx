"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { FieldDefaultValue, SheetPreviewResponse } from "@/lib/types";

// All user-facing sample fields. Must match SAMPLE_FIELDS + DEFAULTABLE_SAMPLE_FIELDS
// on the backend. Fields marked defaultable can have experiment-level defaults set.
const ALL_SAMPLE_FIELDS = [
  { value: "sample_id_unique", label: "Sample ID", defaultable: false },
  { value: "organism", label: "Organism", defaultable: true },
  { value: "tissue_type", label: "Tissue Type", defaultable: true },
  { value: "donor_source", label: "Donor ID", defaultable: true },
  { value: "treatment_condition", label: "Treatment Condition", defaultable: true },
  { value: "chemistry_version", label: "Chemistry Version", defaultable: true },
  { value: "viability_pct", label: "Viability %", defaultable: false },
  { value: "cell_count", label: "Cell Count", defaultable: false },
  { value: "prep_notes", label: "Prep Notes", defaultable: false },
  { value: "molecule_type", label: "Molecule Type", defaultable: true },
  { value: "library_prep_method", label: "Library Prep Method", defaultable: true },
  { value: "library_layout", label: "Library Layout", defaultable: true },
  { value: "qc_status", label: "QC Status", defaultable: false },
  { value: "qc_notes", label: "QC Notes", defaultable: false },
  { value: "collection_timestamp", label: "Collection Timestamp", defaultable: false },
  { value: "collection_method", label: "Collection Method", defaultable: false },
  { value: "sample_batch_code", label: "Sample Batch", defaultable: true },
  { value: "sequencing_batch_code", label: "Sequencing Batch", defaultable: true },
  { value: "sequencing_batch_position", label: "Batch Position", defaultable: false },
];

const DEFAULTABLE_VALUES = new Set(
  ALL_SAMPLE_FIELDS.filter((f) => f.defaultable).map((f) => f.value)
);

interface SheetImportModalProps {
  onClose: () => void;
  onApply: (result: {
    fieldDefaults: FieldDefaultValue[];
    customFields: { name: string; value: string; required: boolean }[];
  }) => void;
  existingFieldDefaults: FieldDefaultValue[];
  existingCustomFields: { name: string; value: string; required: boolean }[];
}

type Step = "url" | "mapping" | "done";

export function SheetImportModal({
  onClose,
  onApply,
  existingFieldDefaults,
  existingCustomFields,
}: SheetImportModalProps) {
  const [step, setStep] = useState<Step>("url");
  const [sheetUrl, setSheetUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<SheetPreviewResponse | null>(null);

  // Column mapping state: header -> target field or action
  // Values: "skip" | "custom:<name>" | a DEFAULTABLE_FIELDS value
  const [columnMappings, setColumnMappings] = useState<Record<string, string>>({});

  // -----------------------------------------------------------------------
  // Step 1: Fetch headers from the sheet
  // -----------------------------------------------------------------------

  async function handlePreview() {
    if (!sheetUrl.trim()) return;
    setLoading(true);
    setError("");
    try {
      const data = await api.post<SheetPreviewResponse>("/api/v1/sheets/preview", {
        sheet_url: sheetUrl.trim(),
      });
      setPreview(data);

      // Default unknown columns to "custom" (create as custom field)
      const defaults: Record<string, string> = {};
      for (const col of data.unknown_columns) {
        defaults[col] = `custom:${col}`;
      }
      setColumnMappings(defaults);

      setStep("mapping");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to read spreadsheet");
    } finally {
      setLoading(false);
    }
  }

  // -----------------------------------------------------------------------
  // Step 2: Column mapping
  // -----------------------------------------------------------------------

  function handleMappingChange(column: string, value: string) {
    setColumnMappings((prev) => ({ ...prev, [column]: value }));
  }

  // Fields already claimed by recognized columns, existing defaults, or other mappings
  const usedFields = new Set<string>([
    ...(preview?.recognized_columns.map((c) => c.mapped_to) ?? []),
    ...existingFieldDefaults.map((d) => d.field_name),
    ...Object.values(columnMappings).filter((v) => v !== "skip" && !v.startsWith("custom:")),
  ]);

  // -----------------------------------------------------------------------
  // Step 3: Apply to form
  // -----------------------------------------------------------------------

  function handleApply() {
    if (!preview) return;

    const newFieldDefaults: FieldDefaultValue[] = [];
    const newCustomFields: { name: string; value: string; required: boolean }[] = [];

    // Add recognized defaultable columns as field defaults (if not already set)
    for (const col of preview.recognized_columns) {
      if (col.defaultable && !existingFieldDefaults.some((d) => d.field_name === col.mapped_to)) {
        newFieldDefaults.push({
          field_name: col.mapped_to,
          default_value: null,
          is_required: null,
        });
      }
      // Non-defaultable recognized fields (sample_id_unique, viability_pct, etc.)
      // don't need any action -- they'll be handled during sample CSV import
    }

    // Process user mappings for unknown columns
    for (const [, mapping] of Object.entries(columnMappings)) {
      if (mapping === "skip") continue;

      if (mapping.startsWith("custom:")) {
        const fieldName = mapping.slice("custom:".length);
        if (!existingCustomFields.some((f) => f.name === fieldName)) {
          newCustomFields.push({ name: fieldName, value: "", required: false });
        }
      } else if (DEFAULTABLE_VALUES.has(mapping)) {
        // Mapped to a defaultable sample field
        if (!existingFieldDefaults.some((d) => d.field_name === mapping)) {
          newFieldDefaults.push({
            field_name: mapping,
            default_value: null,
            is_required: null,
          });
        }
      }
      // Non-defaultable sample field mappings are informational --
      // the user now knows the field exists and will be used on sample import
    }

    onApply({ fieldDefaults: newFieldDefaults, customFields: newCustomFields });
    onClose();
  }

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-3xl w-full max-h-[80vh] overflow-hidden flex flex-col">
        <div className="px-6 py-4 border-b flex justify-between items-center">
          <h2 className="text-lg font-semibold">
            {step === "url" && "Import from Google Sheet"}
            {step === "mapping" && "Map Columns"}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">
            &times;
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {/* Step 1: URL input */}
          {step === "url" && (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">
                Paste a Google Sheets URL to import column headers as sample field defaults
                and custom fields. The sheet must be shared with the bioAF reader service account.
              </p>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Google Sheets URL
                </label>
                <input
                  type="url"
                  value={sheetUrl}
                  onChange={(e) => setSheetUrl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handlePreview();
                    }
                  }}
                  placeholder="https://docs.google.com/spreadsheets/d/..."
                  className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:ring-bioaf-500 focus:border-bioaf-500"
                  autoFocus
                />
              </div>

              <p className="text-xs text-gray-500">
                The sheet&apos;s first row should contain column headers. Share the sheet with the
                reader service account email (found in Settings &gt; Integrations &gt; GCP).
              </p>
            </div>
          )}

          {/* Step 2: Column mapping */}
          {step === "mapping" && preview && (
            <div className="space-y-6">
              <p className="text-sm text-gray-600">
                Found {preview.columns.length} column{preview.columns.length !== 1 ? "s" : ""} in
                sheet &quot;{preview.sheet_name}&quot;.{" "}
                {preview.recognized_columns.length > 0 &&
                  `${preview.recognized_columns.length} matched known fields. `}
                {preview.unknown_columns.length > 0 &&
                  `${preview.unknown_columns.length} need mapping.`}
              </p>

              {/* Recognized columns */}
              {preview.recognized_columns.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-2">Recognized Columns</h3>
                  <p className="text-xs text-gray-500 mb-2">
                    These columns match existing sample fields and will be handled automatically during sample import.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {preview.recognized_columns.map((col) => {
                      const label = ALL_SAMPLE_FIELDS.find((f) => f.value === col.mapped_to)?.label ?? col.mapped_to;
                      return (
                        <span
                          key={col.header}
                          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                            col.defaultable
                              ? "bg-green-100 text-green-800"
                              : "bg-blue-100 text-blue-800"
                          }`}
                        >
                          {col.header} &rarr; {label}
                          {col.defaultable && (
                            <span className="ml-1 text-green-600" title="Can set a default value">*</span>
                          )}
                        </span>
                      );
                    })}
                  </div>
                  <p className="text-xs text-gray-400 mt-2">
                    <span className="inline-block w-2 h-2 rounded-full bg-green-200 mr-1" />
                    Default value can be set &nbsp;
                    <span className="inline-block w-2 h-2 rounded-full bg-blue-200 mr-1" />
                    Tracked per sample
                  </p>
                </div>
              )}

              {/* Unknown columns */}
              {preview.unknown_columns.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-3">Unmapped Columns</h3>
                  <p className="text-xs text-gray-500 mb-3">
                    For each column, choose to map it to an existing sample field,
                    add it as a custom field, or skip it.
                  </p>
                  <div className="space-y-3">
                    {preview.unknown_columns.map((col) => (
                      <div key={col} className="flex items-center gap-3 bg-gray-50 rounded-md p-3">
                        <span className="text-sm font-mono font-medium text-gray-800 min-w-[140px]">
                          {col}
                        </span>
                        <span className="text-gray-400">&rarr;</span>
                        <select
                          value={columnMappings[col] ?? `custom:${col}`}
                          onChange={(e) => handleMappingChange(col, e.target.value)}
                          className="flex-1 text-sm border border-gray-300 rounded-md px-2 py-1.5"
                        >
                          <option value={`custom:${col}`}>Add as custom field &quot;{col}&quot;</option>
                          <option value="skip">Skip this column</option>
                          <optgroup label="Sample fields (default value can be set)">
                            {ALL_SAMPLE_FIELDS.filter(
                              (f) => f.defaultable && (!usedFields.has(f.value) || columnMappings[col] === f.value)
                            ).map((f) => (
                              <option key={f.value} value={f.value}>
                                {f.label}
                              </option>
                            ))}
                          </optgroup>
                          <optgroup label="Sample fields (tracked per sample)">
                            {ALL_SAMPLE_FIELDS.filter(
                              (f) => !f.defaultable && (!usedFields.has(f.value) || columnMappings[col] === f.value)
                            ).map((f) => (
                              <option key={f.value} value={f.value}>
                                {f.label}
                              </option>
                            ))}
                          </optgroup>
                        </select>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* All recognized, no unknowns */}
              {preview.unknown_columns.length === 0 && preview.recognized_columns.length > 0 && (
                <div className="bg-green-50 border border-green-200 rounded-md p-3">
                  <p className="text-sm text-green-700">
                    All columns matched known sample fields. Click &quot;Apply to Form&quot; to populate your experiment.
                  </p>
                </div>
              )}

              {/* All skipped/empty */}
              {preview.columns.length === 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-md p-3">
                  <p className="text-sm text-amber-700">
                    No columns found in the first row of this sheet.
                  </p>
                </div>
              )}
            </div>
          )}

          {/* Error display */}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-md p-3 mt-4">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}
        </div>

        {/* Footer actions */}
        <div className="px-6 py-4 border-t flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-md hover:bg-gray-50"
          >
            Cancel
          </button>

          {step === "url" && (
            <button
              onClick={handlePreview}
              disabled={loading || !sheetUrl.trim()}
              className="px-4 py-2 text-sm bg-bioaf-600 text-white rounded-md hover:bg-bioaf-700 disabled:opacity-50"
            >
              {loading ? "Reading sheet..." : "Import Columns"}
            </button>
          )}

          {step === "mapping" && (
            <button
              onClick={handleApply}
              className="px-4 py-2 text-sm bg-bioaf-600 text-white rounded-md hover:bg-bioaf-700"
            >
              Apply to Form
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
