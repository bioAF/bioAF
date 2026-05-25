"use client";

import type { ReactNode } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  variant?: "danger" | "default";
  /** When true, the action is in flight: buttons are disabled and the confirm
   * button shows a working state, preventing a confusing no-feedback wait and
   * double submissions. */
  busy?: boolean;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  onConfirm,
  onCancel,
  variant = "default",
  busy = false,
}: ConfirmDialogProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
        <h3 className="text-lg font-semibold mb-2">{title}</h3>
        <div className="text-gray-600 mb-6 space-y-3">
          {typeof message === "string" ? <p>{message}</p> : message}
        </div>
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            disabled={busy}
            className="px-4 py-2 text-gray-700 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            onClick={() => {
              if (!busy) onConfirm();
            }}
            disabled={busy}
            className={`px-4 py-2 text-white rounded disabled:opacity-60 ${
              variant === "danger"
                ? "bg-red-600 hover:bg-red-700"
                : "bg-bioaf-600 hover:bg-bioaf-700"
            }`}
          >
            {busy ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
