"use client";

import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { useFocusTrap } from "@/hooks/useFocusTrap";

interface InputDialogProps {
  open: boolean;
  title: string;
  /** Optional explanatory copy above the field. */
  message?: ReactNode;
  /** Visible label for the input. */
  label: string;
  confirmLabel?: string;
  cancelLabel?: string;
  busyLabel?: string;
  placeholder?: string;
  initialValue?: string;
  type?: "text" | "password";
  /** Render a textarea instead of a single-line input. */
  multiline?: boolean;
  /** Permit submitting an empty value (e.g. clearing a stored key). Default false. */
  allowEmpty?: boolean;
  /** In-flight: disables controls and shows the busy label. */
  busy?: boolean;
  /** Inline error message shown under the field. */
  error?: string | null;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

/**
 * A small labelled input modal: the styled replacement for `window.prompt()`.
 * Accessible (role="dialog" + aria-modal, labelled by its title, Escape to cancel,
 * autofocused field) and consistent with ConfirmDialog. Use it whenever a single
 * value is collected before an action; use `type="password"` for secrets.
 */
export function InputDialog({
  open,
  title,
  message,
  label,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  busyLabel = "Working...",
  placeholder,
  initialValue = "",
  type = "text",
  multiline = false,
  allowEmpty = false,
  busy = false,
  error = null,
  onConfirm,
  onCancel,
}: InputDialogProps) {
  const [value, setValue] = useState(initialValue);
  const fieldRef = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const titleId = useId();
  const fieldId = useId();

  // Reset to the seed value each time the dialog opens, and focus the field.
  useEffect(() => {
    if (open) {
      setValue(initialValue);
      // Focus after paint so the element exists.
      const id = window.setTimeout(() => fieldRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
  }, [open, initialValue]);

  // Above the early return: hooks must run on every render, and this
  // component bails out with `return null` when closed.
  const trapRef = useFocusTrap<HTMLDivElement>(open);

  if (!open) return null;

  const canConfirm = !busy && (allowEmpty || value.trim().length > 0);

  const submit = () => {
    if (canConfirm) onConfirm(value);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      if (!busy) onCancel();
    } else if (e.key === "Enter" && !multiline) {
      e.preventDefault();
      submit();
    }
  };


  const fieldClass =
    "w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-bioaf-500 focus:outline-none focus:ring-1 focus:ring-bioaf-500";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onKeyDown={onKeyDown}
    >
      <div
        ref={trapRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
      >
        <h3 id={titleId} className="mb-2 text-lg font-semibold">
          {title}
        </h3>
        {message && <div className="mb-4 space-y-2 text-sm text-gray-600">{message}</div>}
        <label htmlFor={fieldId} className="mb-1 block text-sm font-medium text-gray-700">
          {label}
        </label>
        {multiline ? (
          <textarea
            id={fieldId}
            ref={fieldRef}
            rows={3}
            value={value}
            placeholder={placeholder}
            disabled={busy}
            onChange={(e) => setValue(e.target.value)}
            className={fieldClass}
          />
        ) : (
          <input
            id={fieldId}
            ref={fieldRef}
            type={type}
            value={value}
            placeholder={placeholder}
            disabled={busy}
            onChange={(e) => setValue(e.target.value)}
            className={fieldClass}
          />
        )}
        {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onCancel}
            disabled={busy}
            className="rounded bg-gray-100 px-4 py-2 text-gray-700 hover:bg-gray-200 disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            onClick={submit}
            disabled={!canConfirm}
            className="rounded bg-bioaf-600 px-4 py-2 text-white hover:bg-bioaf-700 disabled:opacity-60"
          >
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
