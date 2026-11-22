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
}

interface PendingUploadsResponse {
  uploads: { upload_id: string; reference: string; state: string }[];
}

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
  const [cancelling, setCancelling] = useState<number | null>(null);

  const patch = (idx: number, values: Partial<FilingItem>) =>
    setItems((prev) => prev.map((item, i) => (i === idx ? ({ ...item, ...values } as T) : item)));

  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => {
      api
        .get<PendingUploadsResponse>("/api/files/uploads/pending")
        .then((data) => {
          const byId = new Map(data.uploads.map((u) => [u.upload_id, u]));
          let filed = false;
          setItems((prev) =>
            prev.map((item) => {
              if (!following(item) || !item.uploadId) return item;
              const found = byId.get(item.uploadId);
              if (!found) {
                filed = true;
                return { ...item, status: "complete", message: undefined } as T;
              }
              if (found.state === "needs_attention") {
                return { ...item, status: "needs_attention", message: exhaustedMessage(found.reference) } as T;
              }
              return item;
            }),
          );
          if (filed) onFiledRef.current();
        })
        .catch(() => {
          // Keep what is shown; the next poll tries again.
        });
    }, PENDING_POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, setItems]);

  const retryNow = async (idx: number) => {
    const item = items[idx];
    if (!item.uploadId) return;
    try {
      const result = await api.post<FileResponse | ReceivedNotFiled>(`/api/files/uploads/${item.uploadId}/retry`, {});
      if (receivedNotFiled(result)) {
        patch(idx, { message: result.detail });
      } else {
        patch(idx, { status: "complete", message: undefined });
        onFiledRef.current();
      }
    } catch (err) {
      patch(idx, { message: err instanceof Error ? err.message : exhaustedMessage(item.reference) });
    }
  };

  const cancelUpload = async (idx: number) => {
    const item = items[idx];
    setCancelling(null);
    if (!item.uploadId) return;
    try {
      await api.post(`/api/files/uploads/${item.uploadId}/cancel`, {});
      patch(idx, { status: "cancelled", message: undefined });
    } catch (err) {
      patch(idx, { message: err instanceof Error ? err.message : "The upload could not be cancelled." });
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
