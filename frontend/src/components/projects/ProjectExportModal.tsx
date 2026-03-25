"use client";

import { useState } from "react";
import { getToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface ExperimentEstimate {
  experiment_id: number;
  name: string;
  total_bytes: number;
  breakdown: Record<string, number>;
}

interface ProjectSizeData {
  total_bytes: number;
  breakdown: Record<string, number>;
  experiments: ExperimentEstimate[];
}

interface GcsResponse {
  signed_url: string;
  expires_in_hours: number;
}

interface ProjectExportModalProps {
  projectId: number;
  projectName: string;
  isOpen: boolean;
  onClose: () => void;
}

type Step = "options" | "size" | "download";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

export function ProjectExportModal({ projectId, projectName, isOpen, onClose }: ProjectExportModalProps) {
  const [step, setStep] = useState<Step>("options");
  const [includeFastq, setIncludeFastq] = useState(false);
  const [includeProvenance, setIncludeProvenance] = useState(true);
  const [sizeData, setSizeData] = useState<ProjectSizeData | null>(null);
  const [sizeLoading, setSizeLoading] = useState(false);
  const [sizeError, setSizeError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const [signedUrl, setSignedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  function handleClose() {
    setStep("options");
    setSizeData(null);
    setSizeError("");
    setDownloadError("");
    setSignedUrl(null);
    setCopied(false);
    onClose();
  }

  async function calculateSize() {
    setSizeLoading(true);
    setSizeError("");
    try {
      const token = getToken();
      const resp = await fetch(
        `${API_URL}/api/projects/${projectId}/export/estimate?include_fastq=${includeFastq}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (!resp.ok) throw new Error(`Error ${resp.status}`);
      const data: ProjectSizeData = await resp.json();
      setSizeData(data);
      setStep("size");
    } catch (err: unknown) {
      setSizeError(err instanceof Error ? err.message : "Failed to calculate size");
    } finally {
      setSizeLoading(false);
    }
  }

  async function startDirectDownload() {
    setDownloading(true);
    setDownloadError("");
    setStep("download");
    try {
      const token = getToken();
      const resp = await fetch(`${API_URL}/api/projects/${projectId}/export/data`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          delivery_method: "direct",
          include_fastq: includeFastq,
          include_provenance: includeProvenance,
        }),
      });
      if (!resp.ok) throw new Error(`Error ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `project_${projectId}_export.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setDownloadError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  async function getSignedUrl() {
    setDownloading(true);
    setDownloadError("");
    setStep("download");
    try {
      const token = getToken();
      const resp = await fetch(`${API_URL}/api/projects/${projectId}/export/data`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          delivery_method: "gcs",
          include_fastq: includeFastq,
          include_provenance: includeProvenance,
        }),
      });
      if (!resp.ok) throw new Error(`Error ${resp.status}`);
      const data: GcsResponse = await resp.json();
      setSignedUrl(data.signed_url);
    } catch (err: unknown) {
      setDownloadError(err instanceof Error ? err.message : "Failed to generate download link");
    } finally {
      setDownloading(false);
    }
  }

  async function copyLink() {
    if (!signedUrl) return;
    await navigator.clipboard.writeText(signedUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const isLarge = sizeData ? sizeData.total_bytes > 1_073_741_824 : false;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">Export Project Data</h2>
          <button onClick={handleClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">
            &times;
          </button>
        </div>
        <p className="text-sm text-gray-600">{projectName}</p>

        {/* Step 1: Options */}
        {step === "options" && (
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={includeFastq}
                  onChange={(e) => setIncludeFastq(e.target.checked)}
                  className="rounded"
                />
                <span className="text-sm text-gray-800">Include FASTQ files</span>
              </label>
              <p className="text-xs text-gray-500 ml-7">Raw sequencing reads. Can be very large.</p>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={includeProvenance}
                  onChange={(e) => setIncludeProvenance(e.target.checked)}
                  className="rounded"
                />
                <span className="text-sm text-gray-800">Include provenance report</span>
              </label>
              <p className="text-xs text-gray-500 ml-7">Full audit trail in JSON, Markdown, PDF, and CSV.</p>
            </div>
            {sizeError && <p className="text-sm text-red-600">{sizeError}</p>}
            <div className="flex justify-end gap-3">
              <button onClick={handleClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900">
                Cancel
              </button>
              <button
                onClick={calculateSize}
                disabled={sizeLoading}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
              >
                {sizeLoading ? "Calculating..." : "Calculate Size"}
              </button>
            </div>
          </div>
        )}

        {/* Step 2: Size display */}
        {step === "size" && sizeData && (
          <div className="space-y-4">
            <div className="bg-gray-50 rounded-md p-4 space-y-3">
              <div className="flex justify-between text-sm font-medium">
                <span>Total size</span>
                <span>{formatBytes(sizeData.total_bytes)}</span>
              </div>
              {sizeData.experiments.length > 0 && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">By experiment</p>
                  {sizeData.experiments.map((exp) => (
                    <div key={exp.experiment_id} className="flex justify-between text-xs text-gray-600">
                      <span className="truncate max-w-xs">{exp.name}</span>
                      <span className="shrink-0 ml-2">{formatBytes(exp.total_bytes)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {isLarge && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                This export is larger than 1 GB. We recommend using &ldquo;Get Download Link&rdquo; instead of downloading
                directly.
              </p>
            )}
            {downloadError && <p className="text-sm text-red-600">{downloadError}</p>}
            <div className="flex justify-between gap-3">
              <button onClick={() => setStep("options")} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900">
                Back
              </button>
              <div className="flex gap-3">
                <button
                  onClick={startDirectDownload}
                  className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700"
                >
                  Download Now
                </button>
                <button
                  onClick={getSignedUrl}
                  className="px-4 py-2 text-sm bg-gray-100 text-gray-800 rounded-md hover:bg-gray-200"
                >
                  Get Download Link
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Step 3: Download */}
        {step === "download" && (
          <div className="space-y-4">
            {downloading && (
              <div className="flex flex-col items-center gap-3 py-4">
                <div className="w-8 h-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin" />
                <p className="text-sm text-gray-600">Preparing your export...</p>
              </div>
            )}
            {!downloading && signedUrl && (
              <div className="space-y-3">
                <p className="text-sm text-green-700 font-medium">Your download link is ready.</p>
                <div className="bg-gray-50 rounded p-3 text-xs text-gray-700 break-all font-mono">{signedUrl}</div>
                <p className="text-xs text-gray-500">This link expires in 24 hours.</p>
                <div className="flex gap-3">
                  <button
                    onClick={copyLink}
                    className="px-4 py-2 text-sm bg-gray-100 text-gray-800 rounded-md hover:bg-gray-200"
                  >
                    {copied ? "Copied!" : "Copy Link"}
                  </button>
                  <a
                    href={signedUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700"
                  >
                    Download
                  </a>
                </div>
              </div>
            )}
            {!downloading && !signedUrl && !downloadError && (
              <p className="text-sm text-green-700">Download started. Check your browser&apos;s downloads.</p>
            )}
            {downloadError && <p className="text-sm text-red-600">{downloadError}</p>}
            <div className="flex justify-end">
              <button onClick={handleClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900">
                Close
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
