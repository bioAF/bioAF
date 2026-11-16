"use client";

import { useState } from "react";
import { NotSet } from "@/components/shared/NotSet";
import { formatBytes } from "@/lib/bytes";
import type { FileVerification } from "@/lib/types/files";

// Plain labels for the persisted verification states (backend: file_verification_service.py).
const STATUS_LABELS: Record<string, string> = {
  waiting_expectation: "Waiting for expected checksum",
  not_required: "No checksum to compare",
  queued: "Queued for verification",
  preparing: "Preparing a protected copy",
  verifying: "Verifying",
  verified: "Verified",
  checksum_mismatch: "Checksum mismatch",
  failed: "Verification failed",
  unverified_legacy: "Not verified (recorded before verification)",
};

// Safe reasons for error codes (backend: FAILURE_MESSAGES). Technical detail stays in the logs.
const REASONS: Record<string, string> = {
  checksum_mismatch: "The file's checksum does not match the expected value.",
  storage_unavailable: "Storage could not be read to verify the file.",
  object_missing: "The file's stored object could not be found.",
  object_changed: "The stored file changed while it was being verified.",
  storage_permission_denied: "bioAF is not allowed to read the stored file.",
  nfs_separation_unavailable:
    "Protected storage for verification is not configured.",
  nfs_capacity_insufficient:
    "There is not enough storage space to verify the file.",
  managed_copy_refused: "The file could not be copied safely for verification.",
  verification_timeout: "Verifying the file took too long.",
  expectation_discovery_failed:
    "The file's expected checksum could not be read.",
  file_deleted: "The file was deleted before verification finished.",
  size_mismatch: "The stored file's size does not match what was expected.",
  size_unavailable: "Storage did not report the file's size.",
};

const METHODS: Record<string, string> = {
  provider: "Storage provider checksum",
  streamed: "Read and computed by bioAF",
};

const RETRYABLE_STATES = new Set(["failed", "checksum_mismatch"]);
const IN_PROGRESS = new Set(["preparing", "verifying"]);

function when(value: string | null): string | null {
  if (!value) return null;
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function Checksum({ label, value }: { label: string; value: string | null }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* some browsers block the clipboard without HTTPS */
    }
  };
  return (
    <div className="col-span-2">
      <dt className="text-xs font-medium text-gray-500 uppercase">{label}</dt>
      <dd className="mt-0.5 flex items-center gap-2">
        {value ? (
          <>
            <span className="font-mono text-xs text-gray-900 break-all">
              {value}
            </span>
            <button
              type="button"
              onClick={copy}
              aria-label={`Copy ${label.toLowerCase().replace("md5", "MD5")}`}
              className="text-xs text-bioaf-600 hover:underline shrink-0"
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </>
        ) : (
          <NotSet label={label} />
        )}
      </dd>
    </div>
  );
}

export function FileVerificationDetails({
  externalId,
  verification,
  readyAt,
  canRetry = false,
  onRetry,
  retrying = false,
}: {
  externalId: string | null | undefined;
  verification: FileVerification | null | undefined;
  readyAt: string | null | undefined;
  canRetry?: boolean;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  const v = verification ?? null;
  const reason = v?.error_code
    ? (REASONS[v.error_code] ?? "The file could not be verified.")
    : null;
  const scheduled =
    v != null &&
    !RETRYABLE_STATES.has(v.status) &&
    v.error_code != null &&
    v.retryable === true;

  return (
    <dl
      className="px-4 pb-4 grid grid-cols-2 gap-x-4 gap-y-3"
      aria-label="Identity and verification"
    >
      <div>
        <dt className="text-xs font-medium text-gray-500 uppercase">
          External ID
        </dt>
        <dd className="mt-0.5 text-sm text-gray-900 break-all">
          {externalId ? externalId : <NotSet label="External ID" />}
        </dd>
      </div>
      <div>
        <dt className="text-xs font-medium text-gray-500 uppercase">
          Ready at
        </dt>
        <dd className="mt-0.5 text-sm text-gray-900" data-testid="ready-at">
          {when(readyAt ?? null) ?? <NotSet label="Ready at" />}
        </dd>
      </div>
      <div className="col-span-2">
        <dt className="text-xs font-medium text-gray-500 uppercase">
          Checksum verification
        </dt>
        <dd className="mt-0.5 text-sm text-gray-900">
          {v
            ? (STATUS_LABELS[v.status] ?? v.status)
            : "No checksum verification recorded"}
        </dd>
      </div>
      {v && IN_PROGRESS.has(v.status) && v.total_bytes != null && (
        <div className="col-span-2">
          <dt className="text-xs font-medium text-gray-500 uppercase">
            Progress
          </dt>
          <dd className="mt-0.5 text-sm text-gray-900">
            {`${formatBytes(v.bytes_processed ?? 0, "file", { missing: "0 B" })} of ${formatBytes(v.total_bytes, "file", { missing: "-" })}`}
          </dd>
        </div>
      )}
      {v &&
        v.status !== "not_required" &&
        v.status !== "waiting_expectation" && (
          <>
            {v.status !== "unverified_legacy" && (
              <Checksum label="Expected MD5" value={v.expected_checksum} />
            )}
            <Checksum label="Observed MD5" value={v.observed_checksum} />
          </>
        )}
      {v?.method && (
        <div>
          <dt className="text-xs font-medium text-gray-500 uppercase">
            Method
          </dt>
          <dd className="mt-0.5 text-sm text-gray-900">
            {METHODS[v.method] ?? v.method}
          </dd>
        </div>
      )}
      {v?.verified_at && (
        <div>
          <dt className="text-xs font-medium text-gray-500 uppercase">
            Verified at
          </dt>
          <dd
            className="mt-0.5 text-sm text-gray-900"
            data-testid="verified-at"
          >
            {when(v.verified_at)}
          </dd>
        </div>
      )}
      {reason && (
        <div className="col-span-2" role="status">
          <dd className="text-sm text-red-700">{reason}</dd>
          {scheduled && (
            <dd className="text-xs text-gray-600 mt-0.5">
              The check will be retried automatically.
            </dd>
          )}
        </div>
      )}
      {v && RETRYABLE_STATES.has(v.status) && canRetry && onRetry && (
        <div className="col-span-2">
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="px-3 py-1.5 border border-gray-300 text-gray-700 rounded-md text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
          >
            Retry verification
          </button>
        </div>
      )}
    </dl>
  );
}
