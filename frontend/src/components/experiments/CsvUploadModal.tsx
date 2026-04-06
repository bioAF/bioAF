"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface RecognizedColumn {
  csv_header: string;
  mapped_to: string;
}

interface PreviewResponse {
  recognized_columns: RecognizedColumn[];
  unknown_columns: string[];
  preview_rows: Record<string, unknown>[];
  total_rows: number;
  errors: string[];
}

interface ConfirmResponse {
  created_count: number;
  error_count: number;
  errors: string[];
  custom_fields_created: string[];
}

// All sample fields that an unknown column can be mapped to
const SAMPLE_FIELDS = [
  { value: "sample_id_external", label: "Sample ID (External)" },
  { value: "organism", label: "Organism" },
  { value: "tissue_type", label: "Tissue Type" },
  { value: "donor_source", label: "Donor Source" },
  { value: "treatment_condition", label: "Treatment Condition" },
  { value: "chemistry_version", label: "Chemistry Version" },
  { value: "viability_pct", label: "Viability %" },
  { value: "cell_count", label: "Cell Count" },
  { value: "prep_notes", label: "Prep Notes" },
  { value: "molecule_type", label: "Molecule Type" },
  { value: "library_prep_method", label: "Library Prep Method" },
  { value: "library_layout", label: "Library Layout" },
  { value: "qc_status", label: "QC Status" },
  { value: "qc_notes", label: "QC Notes" },
  { value: "collection_timestamp", label: "Collection Timestamp" },
  { value: "collection_method", label: "Collection Method" },
  { value: "sample_batch", label: "Sample Batch" },
  { value: "sequencing_batch", label: "Sequencing Batch" },
];

const EXAMPLE_VALUES: Record<string, string> = {
  sample_id_external: "SAMPLE-001",
  organism: "Homo sapiens",
  tissue_type: "PBMC",
  donor_source: "Donor-A",
  treatment_condition: "Control",
  chemistry_version: "v3.1",
  viability_pct: "92.5",
  cell_count: "10000",
  prep_notes: "Standard protocol",
  molecule_type: "total RNA",
  library_prep_method: "10x Chromium 3' v3",
  library_layout: "paired",
  qc_status: "pass",
  qc_notes: "",
  collection_timestamp: "2024-01-15T10:30:00",
  collection_method: "venipuncture",
};

interface Props {
  experimentId: number;
  onClose: () => void;
  onSuccess: () => void;
}

type Step = "select" | "preview" | "confirm" | "done";

export function CsvUploadModal({ experimentId, onClose, onSuccess }: Props) {
  const [step, setStep] = useState<Step>("select");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [columnMappings, setColumnMappings] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ConfirmResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleFileSelect(selectedFile: File) {
    setFile(selectedFile);
    setError("");
    setLoading(true);

    try {
      const data = await api.upload<PreviewResponse>(
        `/api/experiments/${experimentId}/samples/upload/preview`,
        selectedFile,
      );
      setPreview(data);

      if (data.unknown_columns.length > 0) {
        // Initialize mappings: default to "skip" for unknown columns
        const initial: Record<string, string> = {};
        for (const col of data.unknown_columns) {
          initial[col] = "skip";
        }
        setColumnMappings(initial);
        setStep("preview");
      } else if (data.errors.length > 0 && data.total_rows === 0) {
        setError(data.errors.join("; "));
      } else {
        // No unknowns, go straight to confirm
        await submitConfirm(selectedFile, {});
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to parse CSV");
    } finally {
      setLoading(false);
    }
  }

  async function submitConfirm(confirmFile: File, mappings: Record<string, string>) {
    setLoading(true);
    setError("");

    // Filter out "skip" mappings
    const activeMappings: Record<string, string> = {};
    for (const [col, target] of Object.entries(mappings)) {
      if (target !== "skip") {
        activeMappings[col] = target;
      }
    }

    try {
      const data = await api.upload<ConfirmResponse>(
        `/api/experiments/${experimentId}/samples/upload/confirm`,
        confirmFile,
        { column_mappings: JSON.stringify(activeMappings) },
      );
      setResult(data);
      setStep("done");
      if (data.created_count > 0) {
        onSuccess();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create samples");
    } finally {
      setLoading(false);
    }
  }

  function handleMappingChange(column: string, value: string) {
    setColumnMappings((prev) => ({ ...prev, [column]: value }));
  }

  // Fields already claimed by recognized columns or other mappings
  const usedFields = new Set<string>([
    ...(preview?.recognized_columns.map((c) => c.mapped_to) ?? []),
    ...Object.values(columnMappings).filter((v) => v !== "skip" && !v.startsWith("custom:")),
  ]);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-3xl w-full max-h-[80vh] overflow-hidden flex flex-col">
        <div className="px-6 py-4 border-b flex justify-between items-center">
          <h2 className="text-lg font-semibold">
            {step === "select" && "Upload Sample CSV"}
            {step === "preview" && "Map Unknown Columns"}
            {step === "done" && "Upload Complete"}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">
            &times;
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {/* Step 1: File selection */}
          {step === "select" && (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">
                Upload a CSV or TSV file to bulk-create samples. Your CSV should include a
                header row with column names matching the fields below.
              </p>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-medium text-gray-700">Expected CSV Format</h3>
                  <button
                    onClick={() => api.download(`/api/experiments/${experimentId}/samples/csv-template`)}
                    className="text-xs text-bioaf-600 hover:underline"
                  >
                    Download template CSV
                  </button>
                </div>
                <div className="overflow-x-auto border rounded-md">
                  <table className="min-w-full text-xs">
                    <thead className="bg-gray-50">
                      <tr>
                        {SAMPLE_FIELDS.map((f) => (
                          <th key={f.value} className="px-2 py-1.5 text-left font-medium text-gray-600 whitespace-nowrap">
                            {f.value}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="text-gray-400 italic">
                        {SAMPLE_FIELDS.map((f) => (
                          <td key={f.value} className="px-2 py-1 whitespace-nowrap">
                            {EXAMPLE_VALUES[f.value] ?? ""}
                          </td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  All columns are optional. Unrecognized columns will prompt you to map or skip them.
                </p>
              </div>

              <label className="flex items-center justify-center w-full h-24 border-2 border-dashed border-gray-300 rounded-lg cursor-pointer hover:border-bioaf-400 hover:bg-gray-50 transition-colors">
                <div className="text-center">
                  {loading ? (
                    <p className="text-sm text-gray-500">Analyzing file...</p>
                  ) : (
                    <>
                      <p className="text-sm font-medium text-gray-700">
                        Click to select a CSV file
                      </p>
                      <p className="text-xs text-gray-500 mt-1">
                        Supports .csv, .tsv, and .txt
                      </p>
                    </>
                  )}
                </div>
                <input
                  type="file"
                  accept=".csv,.tsv,.txt"
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files?.[0]) handleFileSelect(e.target.files[0]);
                  }}
                  disabled={loading}
                />
              </label>

              {error && (
                <div className="bg-red-50 border border-red-200 rounded-md p-3">
                  <p className="text-sm text-red-700">{error}</p>
                </div>
              )}
            </div>
          )}

          {/* Step 2: Map unknown columns */}
          {step === "preview" && preview && (
            <div className="space-y-6">
              <div>
                <p className="text-sm text-gray-600 mb-2">
                  Found {preview.total_rows} row{preview.total_rows !== 1 ? "s" : ""}.{" "}
                  {preview.recognized_columns.length} column{preview.recognized_columns.length !== 1 ? "s" : ""} recognized,{" "}
                  {preview.unknown_columns.length} need mapping.
                </p>
              </div>

              {preview.recognized_columns.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-2">Recognized Columns</h3>
                  <div className="flex flex-wrap gap-2">
                    {preview.recognized_columns.map((col) => (
                      <span
                        key={col.csv_header}
                        className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800"
                      >
                        {col.csv_header} &rarr; {col.mapped_to}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <h3 className="text-sm font-medium text-gray-700 mb-3">Unknown Columns</h3>
                <p className="text-xs text-gray-500 mb-3">
                  For each unknown column, choose to map it to an existing sample field,
                  accept it as a custom field, or skip it.
                </p>
                <div className="space-y-3">
                  {preview.unknown_columns.map((col) => (
                    <div key={col} className="flex items-center gap-3 bg-gray-50 rounded-md p-3">
                      <span className="text-sm font-mono font-medium text-gray-800 min-w-[140px]">
                        {col}
                      </span>
                      <span className="text-gray-400">&rarr;</span>
                      <select
                        value={columnMappings[col] ?? "skip"}
                        onChange={(e) => handleMappingChange(col, e.target.value)}
                        className="flex-1 text-sm border border-gray-300 rounded-md px-2 py-1.5"
                      >
                        <option value="skip">Skip this column</option>
                        <option value={`custom:${col}`}>Accept as custom field &quot;{col}&quot;</option>
                        <optgroup label="Map to sample field">
                          {SAMPLE_FIELDS.filter(
                            (f) => !usedFields.has(f.value) || columnMappings[col] === f.value
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

              {preview.preview_rows.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-2">
                    Preview (first {preview.preview_rows.length} rows)
                  </h3>
                  <div className="overflow-x-auto border rounded-md">
                    <table className="min-w-full text-xs">
                      <thead className="bg-gray-50">
                        <tr>
                          {Object.keys(preview.preview_rows[0]).map((key) => (
                            <th key={key} className="px-3 py-2 text-left font-medium text-gray-600">
                              {key}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.preview_rows.map((row, i) => (
                          <tr key={i} className="border-t">
                            {Object.values(row).map((val, j) => (
                              <td key={j} className="px-3 py-1.5 text-gray-700">
                                {val != null ? String(val) : ""}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {preview.errors.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-md p-3">
                  <p className="text-sm font-medium text-amber-800 mb-1">Parse Warnings</p>
                  <ul className="text-xs text-amber-700 list-disc list-inside">
                    {preview.errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}

              {error && (
                <div className="bg-red-50 border border-red-200 rounded-md p-3">
                  <p className="text-sm text-red-700">{error}</p>
                </div>
              )}
            </div>
          )}

          {/* Step 3: Results */}
          {step === "done" && result && (
            <div className="space-y-4">
              <div className={`rounded-md p-4 ${result.error_count > 0 ? "bg-amber-50 border border-amber-200" : "bg-green-50 border border-green-200"}`}>
                <p className={`text-sm font-medium ${result.error_count > 0 ? "text-amber-800" : "text-green-800"}`}>
                  Created {result.created_count} sample{result.created_count !== 1 ? "s" : ""}
                  {result.error_count > 0 && ` with ${result.error_count} error${result.error_count !== 1 ? "s" : ""}`}
                </p>
              </div>

              {result.custom_fields_created.length > 0 && (
                <div className="bg-blue-50 border border-blue-200 rounded-md p-3">
                  <p className="text-sm text-blue-800">
                    Custom fields created: {result.custom_fields_created.join(", ")}
                  </p>
                </div>
              )}

              {result.errors.length > 0 && (
                <div>
                  <p className="text-sm font-medium text-gray-700 mb-1">Errors</p>
                  <ul className="text-xs text-red-700 list-disc list-inside bg-red-50 rounded-md p-3">
                    {result.errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t bg-gray-50 flex justify-end gap-3">
          {step === "preview" && (
            <>
              <button
                onClick={() => { setStep("select"); setPreview(null); setFile(null); setError(""); }}
                className="px-4 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
                disabled={loading}
              >
                Back
              </button>
              <button
                onClick={() => file && submitConfirm(file, columnMappings)}
                className="px-4 py-2 text-sm text-white bg-bioaf-600 rounded-md hover:bg-bioaf-700 disabled:opacity-50"
                disabled={loading}
              >
                {loading ? "Creating samples..." : `Create ${preview?.total_rows ?? 0} Samples`}
              </button>
            </>
          )}
          {step === "done" && (
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-white bg-bioaf-600 rounded-md hover:bg-bioaf-700"
            >
              Done
            </button>
          )}
          {step === "select" && (
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
