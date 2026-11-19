"use client";

import { Modal } from "@/components/shared/Modal";
import { useState, useEffect } from "react";
import { api, ApiError } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { statusBadgeClass, statusLabel } from "@/lib/statusStyles";
import type {
  GeoValidationReport,
  GeoValidationSummary,
  GeoValidationField,
  GeoSampleValidation,
  PipelineRun,
  PipelineRunListResponse,
} from "@/lib/types";
import type { GeoFileAssociationFinding } from "@/lib/types/files";

interface GeoExportModalProps {
  experimentId: number;
  isOpen: boolean;
  onClose: () => void;
  userRole: string;
}

function ValidationBar({ summary }: { summary: GeoValidationSummary }) {
  const total = summary.total_fields || 1;
  const segments = [
    { key: "complete", count: summary.complete },
    { key: "populated_unvalidated", count: summary.populated_unvalidated },
    { key: "missing_required", count: summary.missing_required },
    { key: "missing_recommended", count: summary.missing_recommended },
  ];

  return (
    <div className="space-y-2">
      <div className="flex h-4 rounded-full overflow-hidden">
        {segments.map((seg) =>
          seg.count > 0 ? (
            <div
              key={seg.key}
              className={`${statusBadgeClass("geoValidation", seg.key)}`}
              style={{ width: `${(seg.count / total) * 100}%` }}
              title={`${statusLabel("geoValidation", seg.key)}: ${seg.count}`}
            />
          ) : null,
        )}
      </div>
      <div className="flex flex-wrap gap-3 text-xs">
        {segments.map((seg) => (
          <div key={seg.key} className="flex items-center gap-1">
            <span className={`inline-block w-3 h-3 rounded ${statusBadgeClass("geoValidation", seg.key)}`} />
            <span>{statusLabel("geoValidation", seg.key)}: {seg.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function FieldStatusIcon({ status }: { status: string }) {
  switch (status) {
    case "complete":
      return <span className="text-green-600">OK</span>;
    case "populated_unvalidated":
      return <span className="text-yellow-600">?</span>;
    case "missing_required":
      return <span className="text-red-600">X</span>;
    case "missing_recommended":
      return <span className="text-gray-500">--</span>;
    default:
      return <span>{status}</span>;
  }
}

function FileAssociationFinding({
  finding,
  busy,
  onLink,
  onKeep,
}: {
  finding: GeoFileAssociationFinding;
  busy: boolean;
  onLink: (sampleId: number) => void;
  onKeep: (reason: string) => void;
}) {
  const [keeping, setKeeping] = useState(false);
  const [reason, setReason] = useState("");
  const reasonId = `keep-reason-${finding.file_id}`;
  const pending = finding.samples.filter((s) => !s.acknowledged);

  return (
    <li className="bg-gray-50 rounded-md p-3 text-sm space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs break-all">{finding.filename}</span>
        {finding.resolved ? (
          <span className="text-green-600 text-xs">Resolved</span>
        ) : (
          <span className="text-red-600 text-xs">Needs review</span>
        )}
      </div>
      <p className="text-gray-600">
        This {finding.file_kind} file has no Sample links. Its name matches{" "}
        {finding.samples.map((s) => s.external_id).join(", ")}.
      </p>
      {!finding.resolved && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {pending.map((s) => (
              <button
                key={s.sample_id}
                onClick={() => onLink(s.sample_id)}
                disabled={busy}
                className="border border-gray-300 rounded-md px-3 py-1 text-xs hover:bg-white disabled:opacity-50"
              >
                Link to {s.external_id}
              </button>
            ))}
            <button
              onClick={() => setKeeping(true)}
              disabled={busy || keeping}
              className="border border-gray-300 rounded-md px-3 py-1 text-xs hover:bg-white disabled:opacity-50"
            >
              Keep at experiment level
            </button>
          </div>
          {keeping && (
            <div className="space-y-2">
              <label htmlFor={reasonId} className="block text-xs text-gray-700">
                Reason (the file stays listed for the experiment, without a reference on these samples)
              </label>
              <textarea
                id={reasonId}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                className="w-full border border-gray-300 rounded-md px-2 py-1 text-xs"
              />
              <button
                onClick={() => onKeep(reason.trim())}
                disabled={busy || !reason.trim()}
                className="bg-bioaf-600 text-white px-3 py-1 rounded-md text-xs disabled:opacity-50"
              >
                Confirm
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export function GeoExportModal({ experimentId, isOpen, onClose, userRole }: GeoExportModalProps) {
  const [pipelineRuns, setPipelineRuns] = useState<PipelineRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | "">("");
  const [excludeFailedSamples, setExcludeFailedSamples] = useState(true);
  const [validationReport, setValidationReport] = useState<GeoValidationReport | null>(null);
  const [validating, setValidating] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    (async () => {
      try {
        const data = await api.get<PipelineRunListResponse>(
          `/api/pipeline-runs?experiment_id=${experimentId}`,
        );
        setPipelineRuns(data.runs);
        if (data.runs.length > 0) {
          setSelectedRunId(data.runs[0].id);
        }
      } catch {
        // ignore
      }
    })();
  }, [isOpen, experimentId]);

  // Reset state when modal closes
  useEffect(() => {
    if (!isOpen) {
      setValidationReport(null);
      setError(null);
      setValidating(false);
      setDownloading(false);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  // Readiness and download must select the same Samples, so both build their query here and differ
  // only in validate_only. The filter is always explicit: unchecked means include_all, not "omit".
  function exportUrl(validateOnly: boolean): string {
    const params = new URLSearchParams();
    if (validateOnly) params.set("validate_only", "true");
    params.set("pipeline_run_id", String(selectedRunId));
    params.set("qc_status_filter", excludeFailedSamples ? "exclude_failed" : "include_all");
    return `/api/experiments/${experimentId}/export/geo?${params.toString()}`;
  }

  // A report describes one run and one filter; showing it after either changes would mislead.
  function selectRun(runId: number | "") {
    setSelectedRunId(runId);
    setValidationReport(null);
  }

  function selectExcludeFailed(exclude: boolean) {
    setExcludeFailedSamples(exclude);
    setValidationReport(null);
  }

  async function resolve(request: () => Promise<unknown>) {
    setResolving(true);
    setError(null);
    try {
      await request();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The file could not be updated");
      setResolving(false);
      return;
    }
    setResolving(false);
    await handleCheckReadiness({ clearError: false });
  }

  function linkFile(fileId: number, sampleId: number) {
    return resolve(() => api.post(`/api/files/${fileId}/link`, { sample_id: sampleId }));
  }

  function keepAtExperimentLevel(finding: GeoFileAssociationFinding, reason: string) {
    return resolve(() =>
      api.post(`/api/experiments/${experimentId}/export/geo/file-associations/acknowledgments`, {
        file_id: finding.file_id,
        sample_ids: finding.samples.map((s) => s.sample_id),
        fingerprint: finding.fingerprint,
        reason,
      }),
    );
  }

  async function handleCheckReadiness({ clearError = true }: { clearError?: boolean } = {}) {
    if (!selectedRunId) return;
    setValidating(true);
    if (clearError) setError(null);
    setValidationReport(null);
    try {
      const report = await api.post<GeoValidationReport>(
        exportUrl(true),
      );
      setValidationReport(report);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation failed");
    } finally {
      setValidating(false);
    }
  }

  async function handleDownload() {
    if (!selectedRunId) return;
    setDownloading(true);
    setError(null);
    try {
      await api.download(
        exportUrl(false),
        "POST",
        undefined,
        { filename: `geo_export_experiment_${experimentId}.zip` },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
      setDownloading(false);
      // The server found files to resolve: show them, keeping its explanation on screen.
      if (err instanceof ApiError && err.code === "geo_file_associations_unresolved") {
        await handleCheckReadiness({ clearError: false });
      }
      return;
    }
    setDownloading(false);
  }

  const findings = validationReport?.file_association_findings ?? [];
  const downloadBlocked = validationReport?.download_blocked === true;

  return (
    <Modal open title="Export to GEO" onClose={onClose} size="lg">
      <div className="space-y-6">
        {/* Pipeline Run Selector */}
        <div>
          <label htmlFor="pipeline-run" className="block text-sm font-medium text-gray-700 mb-1">
            Pipeline Run
          </label>
          <select id="pipeline-run"
            value={selectedRunId}
            onChange={(e) => selectRun(e.target.value ? Number(e.target.value) : "")}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          >
            <option value="">Select a pipeline run...</option>
            {pipelineRuns.map((run) => (
              <option key={run.id} value={run.id}>
                {run.pipeline_name}
                {run.pipeline_version ? ` v${run.pipeline_version}` : ""}
                {" -- "}
                {run.created_at ? new Date(run.created_at).toLocaleDateString() : ""}
                {" ("}
                {run.status}
                {")"}
              </option>
            ))}
          </select>
        </div>

        {/* QC Filter */}
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="exclude-failed"
            checked={excludeFailedSamples}
            onChange={(e) => selectExcludeFailed(e.target.checked)}
            className="rounded border-gray-300"
          />
          <label htmlFor="exclude-failed" className="text-sm text-gray-700">
            Exclude failed samples
          </label>
        </div>

        {/* Action Buttons */}
        <div className="flex gap-3">
          <button
            onClick={() => handleCheckReadiness()}
            disabled={!selectedRunId || validating}
            className="bg-bioaf-600 text-white px-5 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {validating && <LoadingSpinner size="sm" />}
            Check Readiness
          </button>
          <button
            onClick={handleDownload}
            disabled={!selectedRunId || downloading || downloadBlocked}
            className="bg-green-600 text-white px-5 py-2 rounded-md text-sm hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {downloading && <LoadingSpinner size="sm" />}
            Download Export
          </button>
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-md p-3">
            {error}
          </div>
        )}

        {/* Validation Report */}
        {validationReport && (
          <div className="space-y-6">
            {/* File associations: separate from the metadata summary, and they gate download */}
            {findings.length > 0 && (
              <div>
                <h3 className="text-md font-semibold mb-2">File Associations</h3>
                {downloadBlocked && (
                  <p className="text-sm text-gray-700 mb-2">
                    Resolve these files before downloading: link each one to the sample that owns it, or keep it at
                    experiment level without per-sample references.
                  </p>
                )}
                <ul className="space-y-2">
                  {findings.map((finding) => (
                    <FileAssociationFinding
                      key={finding.file_id}
                      finding={finding}
                      busy={resolving || validating}
                      onLink={(sampleId) => linkFile(finding.file_id, sampleId)}
                      onKeep={(reason) => keepAtExperimentLevel(finding, reason)}
                    />
                  ))}
                </ul>
              </div>
            )}

            {/* Summary */}
            <div>
              <h3 className="text-md font-semibold mb-3">Validation Summary</h3>
              <ValidationBar summary={validationReport.summary} />
            </div>

            {/* Series Fields */}
            {validationReport.series_fields.length > 0 && (
              <div>
                <h3 className="text-md font-semibold mb-2">Series Fields</h3>
                <div className="bg-gray-50 rounded-md overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b">
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">GEO Column</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Value</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Message</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {validationReport.series_fields.map((field, i) => (
                        <tr key={i}>
                          <td className="px-3 py-2 font-mono text-xs">{field.geo_column}</td>
                          <td className="px-3 py-2"><FieldStatusIcon status={field.status} /></td>
                          <td className="px-3 py-2 text-gray-600 truncate max-w-[200px]">{field.value || "--"}</td>
                          <td className="px-3 py-2 text-gray-500 text-xs">{field.message || ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Protocol Fields */}
            {validationReport.protocol_fields.length > 0 && (
              <div>
                <h3 className="text-md font-semibold mb-2">Protocol Fields</h3>
                <div className="bg-gray-50 rounded-md overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="border-b">
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">GEO Column</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Value</th>
                        <th scope="col" className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">Message</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {validationReport.protocol_fields.map((field, i) => (
                        <tr key={i}>
                          <td className="px-3 py-2 font-mono text-xs">{field.geo_column}</td>
                          <td className="px-3 py-2"><FieldStatusIcon status={field.status} /></td>
                          <td className="px-3 py-2 text-gray-600 truncate max-w-[200px]">{field.value || "--"}</td>
                          <td className="px-3 py-2 text-gray-500 text-xs">{field.message || ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Sample Validations */}
            {validationReport.sample_validations.length > 0 && (
              <div>
                <h3 className="text-md font-semibold mb-2">
                  Sample Validations ({validationReport.sample_validations.length} samples)
                </h3>
                <div className="space-y-3">
                  {validationReport.sample_validations.map((sv) => {
                    const missingRequired = sv.fields.filter(
                      (f) => f.status === "missing_required",
                    ).length;
                    const missingRecommended = sv.fields.filter(
                      (f) => f.status === "missing_recommended",
                    ).length;
                    return (
                      <details key={sv.sample_id} className="bg-gray-50 rounded-md">
                        <summary className="px-4 py-2 cursor-pointer flex items-center justify-between text-sm">
                          <span className="font-medium">{sv.sample_name}</span>
                          <span className="text-xs text-gray-500">
                            {missingRequired > 0 && (
                              <span className="text-red-600 mr-2">
                                {missingRequired} required missing
                              </span>
                            )}
                            {missingRecommended > 0 && (
                              <span className="text-gray-500">
                                {missingRecommended} recommended missing
                              </span>
                            )}
                            {missingRequired === 0 && missingRecommended === 0 && (
                              <span className="text-green-600">All fields complete</span>
                            )}
                          </span>
                        </summary>
                        <div className="px-4 pb-3">
                          <table className="min-w-full text-xs">
                            <tbody className="divide-y divide-gray-200">
                              {sv.fields.map((field, i) => (
                                <tr key={i}>
                                  <td className="py-1 font-mono pr-3">{field.geo_column}</td>
                                  <td className="py-1 pr-3">
                                    <FieldStatusIcon status={field.status} />
                                  </td>
                                  <td className="py-1 text-gray-600 truncate max-w-[200px]">
                                    {field.value || "--"}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </details>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
