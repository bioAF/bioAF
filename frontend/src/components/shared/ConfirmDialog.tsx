"use client";

import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { useFocusTrap } from "@/hooks/useFocusTrap";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  variant?: "danger" | "default";
  /** Optional third choice, for decisions with two distinct "yes" outcomes plus a
   * real cancel (e.g. rebuild the image vs reuse the existing one vs do nothing).
   * Omit both and the dialog stays a plain two-button confirm. Cancel always
   * means cancel: Escape and the cancel button never trigger this. */
  secondaryLabel?: string;
  onSecondary?: () => void;
  /** When true, the action is in flight: buttons are disabled and the confirm
   * button shows a working state, preventing a confusing no-feedback wait and
   * double submissions. */
  busy?: boolean;
  /**
   * When set, the user must type this exact phrase before Confirm enables.
   * For the small number of actions that destroy data outright.
   *
   * It lives here rather than being hand-rolled per screen because it was
   * hand-rolled per screen, and inconsistently: on /infrastructure/components,
   * destroying object storage was gated by a checkbox plus a typed "delete my
   * data", while the SAME act on an orphaned bucket was one red button about 500
   * lines away. A user trained by the strong gate reasonably reads the weak one
   * as safe, which makes the inconsistency itself the defect.
   */
  requirePhrase?: string;
}

/**
 * The styled replacement for `window.confirm()`. Accessible in the same way as InputDialog:
 * role="dialog" + aria-modal, labelled by its title, Escape to cancel, and focus moved into the
 * dialog on open. Escape matters especially here: a native confirm() can always be dismissed with
 * it, so a replacement that could not would be a step backwards for keyboard users.
 *
 * Focus lands on Cancel, not the confirm button, so a stray Enter never triggers a destructive
 * action the user has not read yet.
 */
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
  secondaryLabel,
  onSecondary,
  requirePhrase,
}: ConfirmDialogProps) {
  const titleId = useId();
  const phraseId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [phrase, setPhrase] = useState("");

  // Clear the typed phrase whenever the dialog closes, so reopening it never
  // arrives pre-armed from a previous cancel.
  useEffect(() => {
    if (!open) setPhrase("");
  }, [open]);

  const phraseSatisfied = !requirePhrase || phrase.trim() === requirePhrase;

  // Escape is bound to the document, not the dialog subtree, so it works no matter where focus
  // happens to be when the dialog opens.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      if (!busy) onCancel();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, busy, onCancel]);

  // Keep the keyboard inside the dialog and hand focus back to whatever opened
  // it on close. The trap focuses the first control; Cancel is focused
  // explicitly afterwards so the safe choice, not the destructive one, is the
  // default when someone hits Enter straight away.
  const trapRef = useFocusTrap<HTMLDivElement>(open);
  useEffect(() => {
    if (open) cancelRef.current?.focus();
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div
        ref={trapRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="bg-white rounded-lg shadow-xl max-w-md w-full p-6"
      >
        <h3 id={titleId} className="text-lg font-semibold mb-2">{title}</h3>
        <div className="text-gray-600 mb-6 space-y-3">
          {typeof message === "string" ? <p>{message}</p> : message}
        </div>
        {requirePhrase && (
          <div className="mb-6">
            <label htmlFor={phraseId} className="mb-1 block text-xs text-gray-500">
              Type{" "}
              <span className="font-mono font-medium text-gray-700">{requirePhrase}</span>{" "}
              to confirm
            </label>
            <input
              id={phraseId}
              type="text"
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              placeholder={requirePhrase}
              autoComplete="off"
              disabled={busy}
              className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50"
              data-testid="confirm-phrase"
            />
          </div>
        )}
        <div className="flex justify-end gap-3">
          <button
            ref={cancelRef}
            onClick={onCancel}
            disabled={busy}
            className="px-4 py-2 text-gray-700 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          {secondaryLabel && onSecondary && (
            <button
              onClick={() => {
                if (!busy) onSecondary();
              }}
              disabled={busy}
              className="px-4 py-2 text-gray-700 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
            >
              {secondaryLabel}
            </button>
          )}
          <button
            onClick={() => {
              if (!busy && phraseSatisfied) onConfirm();
            }}
            disabled={busy || !phraseSatisfied}
            className={`px-4 py-2 text-white rounded disabled:opacity-60 disabled:cursor-not-allowed ${
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
