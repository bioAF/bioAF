"use client";

import { useState } from "react";
import { NotSet } from "@/components/shared/NotSet";
import { formatBytes } from "@/lib/bytes";
import type { FileVerification } from "@/lib/types/files";

// What each persisted check state means to the person reading it
// (backend: file_verification_service.py). A state is always words, never an empty-value dash.
const STATUS_LABELS: Record<string, string> = {
  waiting_expectation: "Waiting for the supplied checksum",
  not_required: "Not checked: no checksum was provided",
  queued: "Waiting to check file",
  preparing: "Preparing file for checking",
  verifying: "Checking file",
  verified: "Matches supplied checksum",
  checksum_mismatch: "Does not match supplied checksum",
  failed: "Could not check file",
  unverified_legacy: "No verification result recorded",
};
const NO_RESULT = "No verification result recorded";

// Safe reasons for error codes (backend: FAILURE_MESSAGES). Technical detail stays in the logs.
const REASONS: Record<string, string> = {
  checksum_mismatch: "The stored file’s checksum does not match the supplied checksum.",
  storage_unavailable: "Storage could not be read to check the file.",
  object_missing: "The stored file could not be found.",
  object_changed: "The stored file changed while it was being checked.",
  storage_permission_denied: "bioAF is not allowed to read the stored file.",
  nfs_separation_unavailable: "Protected storage for checking files is not set up.",
  nfs_capacity_insufficient: "There is not enough storage space to check the file.",
  managed_copy_refused: "The file could not be copied safely for checking.",
  verification_timeout: "Checking the file took too long.",
  expectation_discovery_failed: "The supplied checksum could not be read.",
  file_deleted: "The file was deleted before the check finished.",
  size_mismatch: "The stored file’s size does not match what was expected.",
  size_unavailable: "Storage did not report the file’s size.",
};

const METHODS: Record<string, string> = {
  provider: "Storage provider checksum",
  streamed: "Read and computed by bioAF",
};

const RETRYABLE_STATES = new Set(["failed", "checksum_mismatch"]);
const IN_PROGRESS = new Set(["preparing", "verifying"]);
// States in which bioAF has not computed the stored file's checksum yet.
const NOT_CHECKED_YET = new Set(["waiting_expectation", "queued", "preparing", "verifying"]);

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

const term = "text-xs font-medium text-gray-500 uppercase";

function Checksum({
  label,
  copyName,
  value,
  pending,
  testId,
}: {
  label: string;
  copyName: string;
  value: string | null;
  pending?: string;
  testId?: string;
}) {
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
      <dt className={term}>{label}</dt>
      <dd className="mt-0.5 flex items-center gap-2" data-testid={testId}>
        {value ? (
          <>
            <span className="font-mono text-xs text-gray-900 break-all">{value}</span>
            <button
              type="button"
              onClick={copy}
              aria-label={`Copy ${copyName}`}
              className="text-xs text-bioaf-600 hover:underline shrink-0"
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </>
        ) : pending ? (
          <span className="text-sm text-gray-700">{pending}</span>
        ) : (
          <NotSet label={label} />
        )}
      </dd>
    </div>
  );
}

/**
 * A file's identity, readiness and checksum check, in the file details panel.
 *
 * The supplied checksum (what an uploader or source stated) and the stored file's checksum (what
 * bioAF read) sit together under one explanation; a checksum recorded before checking existed is
 * labelled as not checked. How it was computed and where the bytes live are technical details.
 */
export function FileVerificationDetails({
  externalId,
  verification,
  readyAt,
  storageUri,
  recordedChecksum,
  canRetry = false,
  onRetry,
  retrying = false,
}: {
  externalId: string | null | undefined;
  verification: FileVerification | null | undefined;
  readyAt: string | null | undefined;
  storageUri?: string | null;
  recordedChecksum?: string | null;
  canRetry?: boolean;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  const v = verification ?? null;
  const reason = v?.error_code ? (REASONS[v.error_code] ?? "The file could not be checked.") : null;
  const scheduled = v != null && !RETRYABLE_STATES.has(v.status) && v.error_code != null && v.retryable === true;
  const legacy = v == null || v.status === "unverified_legacy";
  const legacyChecksum = legacy ? (recordedChecksum ?? v?.observed_checksum ?? null) : null;
  const compared = v != null && !legacy && v.status !== "not_required";

  return (
    <div className="px-4 pb-4">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3" aria-label="Identity and checksum">
        <div>
          <dt className={term}>External ID</dt>
          <dd className="mt-0.5 text-sm text-gray-900 break-all">
            {externalId ? externalId : <NotSet label="External ID" />}
          </dd>
        </div>
        <div>
          <dt className={term}>Ready at</dt>
          <dd className="mt-0.5 text-sm text-gray-900" data-testid="ready-at">
            {when(readyAt ?? null) ?? "Not ready yet"}
          </dd>
        </div>
        <div className="col-span-2">
          <dt className={term}>Checksum</dt>
          <dd className="mt-0.5 text-sm text-gray-900">{v ? (STATUS_LABELS[v.status] ?? v.status) : NO_RESULT}</dd>
          <dd className="mt-0.5 text-xs text-gray-600">
            A checksum is a file fingerprint used to check that the stored file matches the supplied file.
          </dd>
        </div>
        {v && IN_PROGRESS.has(v.status) && v.total_bytes != null && (
          <div className="col-span-2">
            <dt className={term}>Progress</dt>
            <dd className="mt-0.5 text-sm text-gray-900">
              {`${formatBytes(v.bytes_processed ?? 0, "file", { missing: "0 B" })} of ${formatBytes(v.total_bytes, "file", { missing: "-" })}`}
            </dd>
          </div>
        )}
        {compared && (
          <>
            <Checksum
              label="Supplied checksum (MD5)"
              copyName="supplied checksum"
              value={v.expected_checksum}
              pending={v.status === "waiting_expectation" ? "Not read yet" : undefined}
            />
            <Checksum
              label="Stored file checksum (MD5)"
              copyName="stored file checksum"
              value={v.observed_checksum}
              pending={NOT_CHECKED_YET.has(v.status) ? "Not checked yet" : undefined}
              testId="stored-checksum"
            />
          </>
        )}
        {legacyChecksum && (
          <Checksum label="Recorded checksum (MD5), not checked" copyName="recorded checksum" value={legacyChecksum} />
        )}
        {v?.verified_at && (
          <div>
            <dt className={term}>Checked at</dt>
            <dd className="mt-0.5 text-sm text-gray-900" data-testid="verified-at">
              {when(v.verified_at)}
            </dd>
          </div>
        )}
        {reason && (
          <div className="col-span-2" role="status">
            <dd className="text-sm text-red-700">{reason}</dd>
            {scheduled && <dd className="text-xs text-gray-600 mt-0.5">The check will be retried automatically.</dd>}
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
              Check file again
            </button>
          </div>
        )}
      </dl>
      <details className="mt-3">
        <summary className="cursor-pointer text-xs font-medium text-gray-600">Technical details</summary>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-3">
          {v?.method && (
            <div className="col-span-2">
              <dt className={term}>Checksum method</dt>
              <dd className="mt-0.5 text-sm text-gray-900">{METHODS[v.method] ?? v.method}</dd>
            </div>
          )}
          <div className="col-span-2">
            <dt className={term}>Storage location</dt>
            <dd className="mt-0.5 text-xs text-gray-600 font-mono break-all">
              {storageUri ? storageUri : <NotSet label="Storage location" />}
            </dd>
          </div>
        </dl>
      </details>
    </div>
  );
}
