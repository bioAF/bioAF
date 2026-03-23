"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { getCurrentUser, getToken } from "@/lib/auth";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface Experiment {
  id: number;
  name: string;
  status: string;
}

interface SuperSeriesExportModalProps {
  projectId: number;
  experiments: Experiment[];
  isOpen: boolean;
  onClose: () => void;
}

interface ValidationMessage {
  level: "error" | "warning" | "info";
  experiment_id?: number;
  field?: string;
  message: string;
}

interface ValidationResult {
  valid: boolean;
  errors: ValidationMessage[];
  warnings: ValidationMessage[];
  experiment_count: number;
  sample_count: number;
}

export function SuperSeriesExportModal({
  projectId,
  experiments,
  isOpen,
  onClose,
}: SuperSeriesExportModalProps) {
  const [includedIds, setIncludedIds] = useState<Set<number>>(new Set());
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const user = getCurrentUser();
  const canAccess = user?.role_name === "admin" || user?.role_name === "comp_bio";

  // Initialize included IDs when modal opens
  useEffect(() => {
    if (isOpen) {
      setIncludedIds(new Set(experiments.map((e) => e.id)));
      setValidation(null);
      setError(null);
      setValidating(false);
      setDownloading(false);
    }
  }, [isOpen, experiments]);

  const toggleExperiment = useCallback((id: number) => {
    setIncludedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
    // Clear previous validation when selection changes
    setValidation(null);
  }, []);

  const handleValidate = useCallback(async () => {
    if (includedIds.size === 0) return;
    setValidating(true);
    setError(null);
    setValidation(null);
    try {
      const result = await api.post<ValidationResult>(
        `/api/projects/${projectId}/export/geo?validate_only=true`,
        { experiment_ids: Array.from(includedIds) },
      );
      setValidation(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation failed");
    } finally {
      setValidating(false);
    }
  }, [projectId, includedIds]);

  const handleDownload = useCallback(async () => {
    if (includedIds.size === 0) return;
    setDownloading(true);
    setError(null);
    try {
      const url = `${API_URL}/api/projects/${projectId}/export/geo`;
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify({ experiment_ids: Array.from(includedIds) }),
      });

      if (!response.ok) {
        const errBody = await response
          .json()
          .catch(() => ({ detail: "Download failed" }));
        throw new Error(errBody.detail || "Download failed");
      }

      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);

      const contentDisposition = response.headers.get("Content-Disposition");
      let filename = `geo_superseries_project_${projectId}.zip`;
      if (contentDisposition) {
        const match = contentDisposition.match(/filename="?([^";\n]+)"?/);
        if (match) {
          filename = match[1];
        }
      }

      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(blobUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }, [projectId, includedIds]);

  if (!isOpen || !canAccess) return null;

  const hasErrors = validation?.errors && validation.errors.length > 0;
  const downloadDisabled =
    includedIds.size === 0 || downloading || (validation !== null && hasErrors);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      data-testid="superseries-modal"
    >
      <div className="bg-white rounded-lg shadow-xl w-full max-w-3xl max-h-[85vh] flex flex-col mx-4">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b">
          <h2 className="text-xl font-semibold">
            Export GEO SuperSeries
          </h2>
          <button
            onClick={onClose}
            data-testid="modal-close"
            className="text-gray-400 hover:text-gray-600 text-2xl leading-none"
          >
            &times;
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Validation Summary (top) */}
          {validation && (
            <div data-testid="validation-summary">
              {hasErrors && (
                <div className="bg-red-50 border border-red-200 rounded-md p-4 mb-4">
                  <h3 className="text-sm font-semibold text-red-800 mb-2">
                    Validation Errors ({validation.errors.length})
                  </h3>
                  <ul className="text-sm text-red-700 space-y-1">
                    {validation.errors.map((e, i) => (
                      <li key={i} data-testid="validation-error">
                        {e.field ? `${e.field}: ` : ""}
                        {e.message}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {validation.warnings && validation.warnings.length > 0 && (
                <div className="bg-yellow-50 border border-yellow-200 rounded-md p-4 mb-4">
                  <h3 className="text-sm font-semibold text-yellow-800 mb-2">
                    Warnings ({validation.warnings.length})
                  </h3>
                  <ul className="text-sm text-yellow-700 space-y-1">
                    {validation.warnings.map((w, i) => (
                      <li key={i} data-testid="validation-warning">
                        {w.field ? `${w.field}: ` : ""}
                        {w.message}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {!hasErrors &&
                (!validation.warnings || validation.warnings.length === 0) && (
                  <div className="bg-green-50 border border-green-200 rounded-md p-4 mb-4">
                    <p className="text-sm text-green-700 font-medium">
                      All validations passed. Ready to export{" "}
                      {validation.experiment_count} experiment
                      {validation.experiment_count !== 1 ? "s" : ""} with{" "}
                      {validation.sample_count} sample
                      {validation.sample_count !== 1 ? "s" : ""}.
                    </p>
                  </div>
                )}
            </div>
          )}

          {/* Experiment Checkboxes */}
          <div>
            <h3 className="text-sm font-semibold text-gray-700 mb-3">
              Experiments to Include
            </h3>
            <div className="bg-gray-50 rounded-md divide-y divide-gray-200">
              {experiments.map((exp) => (
                <label
                  key={exp.id}
                  className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-100"
                >
                  <input
                    type="checkbox"
                    checked={includedIds.has(exp.id)}
                    onChange={() => toggleExperiment(exp.id)}
                    className="rounded border-gray-300"
                    data-testid={`experiment-checkbox-${exp.id}`}
                  />
                  <span className="text-sm font-medium text-gray-900">
                    {exp.name}
                  </span>
                  <span className="text-xs text-gray-500 ml-auto">
                    {exp.status}
                  </span>
                </label>
              ))}
            </div>
            <p className="text-xs text-gray-500 mt-2">
              {includedIds.size} of {experiments.length} experiment
              {experiments.length !== 1 ? "s" : ""} selected
            </p>
          </div>

          {/* Error */}
          {error && (
            <div
              className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-md p-3"
              data-testid="export-error"
            >
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-6 border-t flex justify-between items-center">
          <button
            onClick={handleValidate}
            disabled={includedIds.size === 0 || validating}
            className="bg-bioaf-600 text-white px-5 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            data-testid="validate-button"
          >
            {validating && <LoadingSpinner size="sm" />}
            {validating ? "Validating..." : "Check Readiness"}
          </button>

          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 border border-gray-300 rounded-md text-sm hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              onClick={handleDownload}
              disabled={!!downloadDisabled}
              className="bg-green-600 text-white px-5 py-2 rounded-md text-sm hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              data-testid="download-button"
            >
              {downloading && <LoadingSpinner size="sm" />}
              {downloading ? "Downloading..." : "Download SuperSeries"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
