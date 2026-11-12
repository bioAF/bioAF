"use client";

import { useId, type ReactNode } from "react";
import { Modal } from "@/components/shared/Modal";
import { Alert } from "@/components/shared/Alert";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { Button } from "@/components/ui/Button";
import { useCapabilities } from "@/hooks/useCapabilities";
import { formatBytes } from "@/lib/bytes";
import { NOT_SET } from "@/lib/placeholders";
import { LARGE_EXPORT_BYTES, type ExportEstimate, type ExportTarget } from "./exportTargets";
import { useDataExport, type DataExportController } from "./useDataExport";

export const LINKS_UNAVAILABLE =
  "Download links are unavailable for this storage, or support could not be confirmed. Use Download Now instead.";

export function formatExportSize(bytes: number): string {
  return formatBytes(bytes, "file", { missing: NOT_SET });
}

interface DataExportDialogProps<E extends ExportEstimate> {
  title: string;
  entityName: string;
  target: ExportTarget<E>;
  isOpen: boolean;
  onClose: () => void;
  /** The entity's own breakdown under the total (file types, experiments). */
  renderBreakdown: (estimate: E) => ReactNode;
}

/** The shared experiment/project data export dialog. */
export function DataExportDialog<E extends ExportEstimate>({
  title,
  entityName,
  target,
  isOpen,
  onClose,
  renderBreakdown,
}: DataExportDialogProps<E>) {
  const exporter = useDataExport(target, isOpen);
  if (!isOpen) return null;
  const { phase } = exporter;

  return (
    <Modal open title={title} onClose={onClose}>
      <div className="space-y-4">
        <p className="text-sm text-gray-600">{entityName}</p>
        {(phase.name === "options" || phase.name === "estimating") && (
          <OptionsStep exporter={exporter} onCancel={onClose} />
        )}
        {phase.name === "estimate" && (
          <SizeStep exporter={exporter} estimate={phase.estimate} renderBreakdown={renderBreakdown} />
        )}
        {phase.name !== "options" && phase.name !== "estimating" && phase.name !== "estimate" && (
          <DeliveryStep exporter={exporter} onClose={onClose} />
        )}
      </div>
    </Modal>
  );
}

function OptionsStep<E extends ExportEstimate>({
  exporter,
  onCancel,
}: {
  exporter: DataExportController<E>;
  onCancel: () => void;
}) {
  const { options, setOption, phase, calculate } = exporter;
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={options.includeFastq}
            onChange={(e) => setOption("includeFastq", e.target.checked)}
            className="rounded"
          />
          <span className="text-sm text-gray-800">Include FASTQ files</span>
        </label>
        <p className="text-xs text-gray-500 ml-7">Raw sequencing reads. Can be very large.</p>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={options.includeProvenance}
            onChange={(e) => setOption("includeProvenance", e.target.checked)}
            className="rounded"
          />
          <span className="text-sm text-gray-800">Include provenance report</span>
        </label>
        <p className="text-xs text-gray-500 ml-7">Full audit trail in JSON, Markdown, PDF, and CSV.</p>
      </div>
      {phase.name === "options" && phase.error && <Alert tone="error">{phase.error}</Alert>}
      <div className="flex justify-end gap-3">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={() => void calculate()} busy={phase.name === "estimating"} busyLabel="Calculating...">
          Calculate Size
        </Button>
      </div>
    </div>
  );
}

function SizeStep<E extends ExportEstimate>({
  exporter,
  estimate,
  renderBreakdown,
}: {
  exporter: DataExportController<E>;
  estimate: E;
  renderBreakdown: (estimate: E) => ReactNode;
}) {
  // Read here rather than on mount: the delivery choice only matters once there is a size.
  const capabilities = useCapabilities();
  const linksSupported = !capabilities.loading && capabilities.has("signed_url_upload");
  const reasonId = useId();
  const isLarge = estimate.total_bytes > LARGE_EXPORT_BYTES;

  return (
    <div className="space-y-4">
      <div className="bg-gray-50 rounded-md p-4 space-y-3">
        <div className="flex justify-between text-sm font-medium">
          <span>Total size</span>
          <span>{formatExportSize(estimate.total_bytes)}</span>
        </div>
        {renderBreakdown(estimate)}
      </div>
      {isLarge && (
        <Alert tone="warning">
          {linksSupported ? (
            <>
              This export is larger than 1 GB. We recommend using &ldquo;Get Download Link&rdquo; instead of
              downloading directly.
            </>
          ) : (
            <>This export is larger than 1 GB. Download Now keeps this tab busy until the archive arrives.</>
          )}
        </Alert>
      )}
      {!linksSupported && (
        <p id={reasonId} className="text-xs text-gray-500">
          {LINKS_UNAVAILABLE}
        </p>
      )}
      <div className="flex justify-between gap-3">
        <Button variant="ghost" onClick={exporter.back}>
          Back
        </Button>
        <div className="flex gap-3">
          <Button onClick={() => void exporter.downloadDirect()}>Download Now</Button>
          <Button
            variant="secondary"
            disabled={!linksSupported}
            aria-describedby={linksSupported ? undefined : reasonId}
            onClick={() => void exporter.requestLink(linksSupported)}
          >
            Get Download Link
          </Button>
        </div>
      </div>
    </div>
  );
}

function DeliveryStep<E extends ExportEstimate>({
  exporter,
  onClose,
}: {
  exporter: DataExportController<E>;
  onClose: () => void;
}) {
  const { phase, copy, copyLink, back } = exporter;
  return (
    <div className="space-y-4">
      {phase.name === "exporting" && (
        <div className="flex flex-col items-center gap-3 py-4">
          <LoadingSpinner label="Preparing your export" />
          <p className="text-sm text-gray-600">Preparing your export...</p>
        </div>
      )}
      {phase.name === "link" && (
        <div className="space-y-3">
          <p className="text-sm text-green-700 font-medium">Your download link is ready.</p>
          <div className="bg-gray-50 rounded p-3 text-xs text-gray-700 break-all font-mono">{phase.link.url}</div>
          {phase.link.expiresInHours !== null && (
            <p className="text-xs text-gray-500">
              This link expires in {phase.link.expiresInHours} {phase.link.expiresInHours === 1 ? "hour" : "hours"}.
            </p>
          )}
          {copy === "unavailable" && (
            <Alert tone="warning">Copying is not available here. Select the link above and copy it.</Alert>
          )}
          <div className="flex gap-3">
            <Button variant="secondary" onClick={() => void copyLink()}>
              {copy === "copied" ? "Copied!" : "Copy Link"}
            </Button>
            <a
              href={phase.link.url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-md hover:bg-indigo-700"
            >
              Download
            </a>
          </div>
        </div>
      )}
      {phase.name === "downloaded" && (
        <p className="text-sm text-green-700">Download started. Check your browser&apos;s downloads.</p>
      )}
      {phase.name === "failed" && <Alert tone="error">{phase.message}</Alert>}
      <div className="flex justify-end gap-3">
        {phase.name === "failed" && (
          <Button variant="ghost" onClick={back}>
            Back
          </Button>
        )}
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      </div>
    </div>
  );
}
