"use client";

import { Modal } from "@/components/shared/Modal";
import { useState } from "react";
import { api } from "@/lib/api";
import type { SheetPreviewResponse } from "@/lib/types";

const ALL_SAMPLE_FIELDS = [
  { value: "external_id", label: "Sample ID" },
  { value: "organism", label: "Organism" },
  { value: "tissue_type", label: "Tissue Type" },
  { value: "donor_source", label: "Donor ID" },
  { value: "treatment_condition", label: "Treatment Condition" },
  { value: "chemistry_version", label: "Chemistry Version" },
  { value: "viability_pct", label: "Viability %" },
  { value: "cell_count", label: "Cell Count" },
  { value: "prep_notes", label: "Prep Notes" },
  { value: "molecule_type", label: "Molecule Type" },
  { value: "library_prep_method", label: "Library Prep Method" },
  { value: "library_layout", label: "Library Layout" },
  { value: "assay", label: "Assay" },
  { value: "qc_status", label: "QC Status" },
  { value: "qc_notes", label: "QC Notes" },
  { value: "sample_batch_code", label: "Sample Batch" },
  { value: "sequencing_batch_code", label: "Sequencing Batch" },
];

interface TemplateSheetImportResult {
  requiredSampleFields: string[];
  customFields: { name: string; type: string; required: boolean }[];
}

interface TemplateSheetImportModalProps {
  onClose: () => void;
  onApply: (result: TemplateSheetImportResult) => void;
  existingRequiredSampleFields: string[];
  existingCustomFields: { name: string; type: string; required: boolean }[];
}

type Step = "url" | "mapping";

export function TemplateSheetImportModal({
  onClose,
  onApply,
  existingRequiredSampleFields,
  existingCustomFields,
}: TemplateSheetImportModalProps) {
  const [step, setStep] = useState<Step>("url");
  const [sheetUrl, setSheetUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<SheetPreviewResponse | null>(null);

  // For each unknown column: "skip" | "custom" | a sample-field value
  const [columnMappings, setColumnMappings] = useState<Record<string, string>>({});
  // For each unknown column the user wants as custom: which type
  const [customTypes, setCustomTypes] = useState<Record<string, string>>({});
  // For each column (recognized or unknown→sample-field): mark as required on the template
  const [requiredFlags, setRequiredFlags] = useState<Record<string, boolean>>({});

  async function handlePreview() {
    if (!sheetUrl.trim()) return;
    setLoading(true);
    setError("");
    try {
      const data = await api.post<SheetPreviewResponse>("/api/v1/sheets/preview", {
        sheet_url: sheetUrl.trim(),
      });
      setPreview(data);

      const mappingDefaults: Record<string, string> = {};
      const typeDefaults: Record<string, string> = {};
      for (const col of data.unknown_columns) {
        mappingDefaults[col] = "custom";
        typeDefaults[col] = "string";
      }
      setColumnMappings(mappingDefaults);
      setCustomTypes(typeDefaults);
      setRequiredFlags({});
      setStep("mapping");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to read spreadsheet");
    } finally {
      setLoading(false);
    }
  }

  function handleApply() {
    if (!preview) return;

    const requiredSampleFields = new Set(existingRequiredSampleFields);
    const customFields = [...existingCustomFields];

    // Recognized sample fields: optionally mark as required
    for (const col of preview.recognized_columns) {
      if (requiredFlags[col.header]) {
        requiredSampleFields.add(col.mapped_to);
      }
    }

    // Unknown columns: route per user choice
    for (const [column, mapping] of Object.entries(columnMappings)) {
      if (mapping === "skip") continue;

      if (mapping === "custom") {
        if (!customFields.some((f) => f.name === column)) {
          customFields.push({
            name: column,
            type: customTypes[column] ?? "string",
            required: !!requiredFlags[column],
          });
        }
      } else {
        // Mapped to a real sample field
        if (requiredFlags[column]) {
          requiredSampleFields.add(mapping);
        }
      }
    }

    onApply({
      requiredSampleFields: Array.from(requiredSampleFields),
      customFields,
    });
    onClose();
  }

  const title =
    step === "url" ? "Import Template from Google Sheet" : "Map Columns to Template";

  return (
    <Modal
      open
      title={title}
      onClose={onClose}
      size="lg"
      footer={
        <>
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
              Apply to Template
            </button>
          )}
        </>
      }
    >
      {step === "url" && (
        <div className="space-y-4">
          <p className="text-sm text-gray-600">
            Paste a Google Sheets URL to seed required sample fields and custom fields
            from its column headers. Share the sheet with the bioAF reader service account.
          </p>
          <div>
            <label htmlFor="google-sheets-url" className="block text-sm font-medium text-gray-700 mb-1">Google Sheets URL</label>
            <input id="google-sheets-url"
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
        </div>
      )}

      {step === "mapping" && preview && (
        <div className="space-y-6">
          <p className="text-sm text-gray-600">
            Found {preview.columns.length} column{preview.columns.length !== 1 ? "s" : ""} in
            sheet &quot;{preview.sheet_name}&quot;. Tick &quot;Required&quot; on any column that
            samples must populate.
          </p>

          {preview.recognized_columns.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-2">Recognized Sample Fields</h3>
              <div className="space-y-2">
                {preview.recognized_columns.map((col) => (
                  <div key={col.header} className="flex items-center gap-3 bg-gray-50 rounded-md p-3">
                    <span className="text-sm font-mono font-medium text-gray-800 min-w-[140px]">
                      {col.header}
                    </span>
                    <span className="text-gray-500">&rarr;</span>
                    <span className="text-sm flex-1">
                      {ALL_SAMPLE_FIELDS.find((f) => f.value === col.mapped_to)?.label ?? col.mapped_to}
                    </span>
                    <label className="flex items-center gap-1 text-sm text-gray-600">
                      <input
                        type="checkbox"
                        checked={!!requiredFlags[col.header]}
                        onChange={(e) =>
                          setRequiredFlags((prev) => ({ ...prev, [col.header]: e.target.checked }))
                        }
                      />
                      Required
                    </label>
                  </div>
                ))}
              </div>
            </div>
          )}

          {preview.unknown_columns.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-3">Unmapped Columns</h3>
              <div className="space-y-3">
                {preview.unknown_columns.map((col) => (
                  <div key={col} className="flex items-center gap-3 bg-gray-50 rounded-md p-3">
                    <span className="text-sm font-mono font-medium text-gray-800 min-w-[140px]">
                      {col}
                    </span>
                    <select
                      aria-label={`Map column ${col} to`}
                      value={columnMappings[col] ?? "custom"}
                      onChange={(e) =>
                        setColumnMappings((prev) => ({ ...prev, [col]: e.target.value }))
                      }
                      className="flex-1 text-sm border border-gray-300 rounded-md px-2 py-1.5"
                    >
                      <option value="custom">Add as custom field &quot;{col}&quot;</option>
                      <option value="skip">Skip</option>
                      {ALL_SAMPLE_FIELDS.map((f) => (
                        <option key={f.value} value={f.value}>
                          Map to {f.label}
                        </option>
                      ))}
                    </select>
                    {(columnMappings[col] ?? "custom") === "custom" && (
                      <select
                        aria-label={`Type for custom field ${col}`}
                        value={customTypes[col] ?? "string"}
                        onChange={(e) =>
                          setCustomTypes((prev) => ({ ...prev, [col]: e.target.value }))
                        }
                        className="text-sm border border-gray-300 rounded-md px-2 py-1.5"
                      >
                        <option value="string">Text</option>
                        <option value="number">Number</option>
                        <option value="date">Date</option>
                      </select>
                    )}
                    {(columnMappings[col] ?? "custom") !== "skip" && (
                      <label className="flex items-center gap-1 text-sm text-gray-600">
                        <input
                          type="checkbox"
                          checked={!!requiredFlags[col]}
                          onChange={(e) =>
                            setRequiredFlags((prev) => ({ ...prev, [col]: e.target.checked }))
                          }
                        />
                        Required
                      </label>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-md p-3 mt-4">
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}
    </Modal>
  );
}
