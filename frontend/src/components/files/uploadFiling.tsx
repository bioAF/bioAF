"use client";

/**
 * Uploads whose bytes are stored but not yet filed (storage lifecycle 11F, mockup M12), shared by
 * every uploader: recognizing the 202 receipt, following the upload until it is filed or retries
 * run out, and the uploader's Retry now and Cancel upload actions.
 */
import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "@/lib/api";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";
import type { components } from "../../../contracts/internal-api";
import type { FileResponse } from "@/lib/types";

export type ReceivedNotFiled = components["schemas"]["UploadReceivedNotFiled"];

/** The statuses filing moves an upload through after its bytes arrived. */
export type FilingStatus = "pending" | "needs_attention" | "complete" | "cancelled";

export interface FilingItem {
  status: string;
  uploadId?: string;
  reference?: string;
  /** The server's plain explanation for a pending or exhausted upload. */
  message?: string;
  error?: string;
}

type UploadOutcome = components["schemas"]["UploadOutcome"];

const PENDING_POLL_MS = 30_000;

export function receivedNotFiled(value: unknown): value is ReceivedNotFiled {
  return (value as { code?: string } | null)?.code === "upload_received_not_filed";
}

/** The item state for a 202 receipt: stored, not yet filed, followed until filed. */
export function pendingFrom(result: ReceivedNotFiled) {
  return {
    status: "pending" as const,
    progress: 100,
    message: result.detail,
    uploadId: result.details.upload_id,
    reference: result.details.reference,
  };
}

function exhaustedMessage(reference: string | undefined): string {
  return `Could not be filed. Your file is still stored. Retry now, or cancel the upload to remove it. Reference ${reference}.`;
}

function failedMessage(reference: string | null | undefined): string {
  return `Could not be filed. Upload the file again. Reference ${reference}.`;
}

/** The item update for an upload's outcome, or null when what is shown still holds. */
function fromOutcome(item: FilingItem, found: UploadOutcome): Partial<FilingItem> | null {
  switch (found.outcome) {
    case "filed":
      return { status: "complete", message: undefined };
    case "cancelled":
      return { status: "cancelled", message: undefined };
    case "failed":
      return { status: "error", message: undefined, error: failedMessage(found.reference ?? item.reference) };
    default:
      return found.state === "needs_attention" && item.status !== "needs_attention"
        ? { status: "needs_attention", message: exhaustedMessage(found.reference ?? item.reference) }
        : null;
  }
}

function following(item: FilingItem): boolean {
  return item.status === "pending" || item.status === "needs_attention";
}

/**
 * Follow received uploads until they are filed (then "complete") or retries run out (then
 * "needs_attention"), and provide Retry now and Cancel upload for them.
 */
export function useUploadFiling<T extends FilingItem>(
  items: T[],
  setItems: Dispatch<SetStateAction<T[]>>,
  onFiled: () => void,
) {
  const waiting = items.some(following);
  const onFiledRef = useRef(onFiled);
  onFiledRef.current = onFiled;
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const [cancelling, setCancelling] = useState<number | null>(null);

  // Responses are applied by upload ID, never by position: rows can be removed while a request
  // waits, and a response for a removed row changes nothing.
  const patch = (uploadId: string, values: Partial<FilingItem>) =>
    setItems((prev) => prev.map((item) => (item.uploadId === uploadId ? ({ ...item, ...values } as T) : item)));

  useEffect(() => {
    if (!waiting) return;
    // Each followed upload's explicit outcome: leaving the pending list does not mean filed, since
    // a cancelled or failed upload leaves it too.
    const timer = setInterval(() => {
      const ids = Array.from(
        new Set(itemsRef.current.filter((item) => following(item) && item.uploadId).map((item) => item.uploadId!)),
      );
      Promise.allSettled(ids.map((id) => api.get<UploadOutcome>(`/api/files/uploads/${encodeURIComponent(id)}`))).then(
        (results) => {
          const byId = new Map<string, UploadOutcome>();
          results.forEach((r) => r.status === "fulfilled" && byId.set(r.value.upload_id, r.value));
          const update = (item: T) => {
            const found = following(item) && item.uploadId ? byId.get(item.uploadId) : undefined;
            return found ? fromOutcome(item, found) : null;
          };
          const filed = itemsRef.current.some((item) => update(item)?.status === "complete");
          setItems((prev) =>
            prev.map((item) => {
              const values = update(item);
              return values ? ({ ...item, ...values } as T) : item;
            }),
          );
          if (filed) onFiledRef.current();
        },
      );
    }, PENDING_POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, setItems]);

  const retryNow = async (idx: number) => {
    const item = items[idx];
    const uploadId = item?.uploadId;
    if (!uploadId) return;
    try {
      const result = await api.post<FileResponse | ReceivedNotFiled>(`/api/files/uploads/${uploadId}/retry`, {});
      if (receivedNotFiled(result)) {
        patch(uploadId, { message: result.detail });
      } else {
        patch(uploadId, { status: "complete", message: undefined });
        onFiledRef.current();
      }
    } catch (err) {
      patch(uploadId, { message: err instanceof Error ? err.message : exhaustedMessage(item.reference) });
    }
  };

  const cancelUpload = async (idx: number) => {
    const item = items[idx];
    setCancelling(null);
    const uploadId = item?.uploadId;
    if (!uploadId) return;
    try {
      await api.post(`/api/files/uploads/${uploadId}/cancel`, {});
      patch(uploadId, { status: "cancelled", message: undefined });
    } catch (err) {
      patch(uploadId, { message: err instanceof Error ? err.message : "The upload could not be cancelled." });
    }
  };

  return { retryNow, cancelUpload, cancelling, requestCancel: setCancelling };
}

/** The pending explanation under a file, with Retry now and Cancel upload once retries ran out. */
export function FilingDetails({
  item,
  onRetry,
  onCancel,
}: {
  item: FilingItem;
  onRetry: () => void;
  onCancel: () => void;
}) {
  return (
    <>
      {following(item) && item.message && <p className="text-xs text-amber-700 mt-1">{item.message}</p>}
      {item.status === "needs_attention" && (
        <div className="mt-1 flex gap-2">
          <Button size="sm" variant="secondary" onClick={onCancel}>
            Cancel upload
          </Button>
          <Button size="sm" onClick={onRetry}>
            Retry now
          </Button>
        </div>
      )}
    </>
  );
}

/** The status label for an upload filing follows, or null for other statuses. */
export function FilingBadge({ status }: { status: string }) {
  if (status === "pending" || status === "needs_attention") {
    return <span className="text-xs font-medium text-amber-700 shrink-0">Not filed yet</span>;
  }
  if (status === "cancelled") {
    return <span className="text-xs font-medium text-gray-500 shrink-0">Cancelled</span>;
  }
  return null;
}

export function CancelUploadDialog({
  destination,
  onKeep,
  onRemove,
}: {
  /** Where the file would have been added (an experiment's name), if known. */
  destination: string | null;
  onKeep: () => void;
  onRemove: () => void;
}) {
  return (
    <Modal
      open
      title="Remove this upload?"
      onClose={onKeep}
      footer={
        <>
          <Button variant="secondary" onClick={onKeep}>
            Keep it
          </Button>
          <Button variant="danger" onClick={onRemove}>
            Remove upload
          </Button>
        </>
      }
    >
      <p className="text-sm text-gray-700">
        Remove this upload? The stored copy is deleted and nothing is added to {destination ?? "the experiment"}.
      </p>
    </Modal>
  );
}
