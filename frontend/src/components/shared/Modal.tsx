"use client";

import { useEffect, useId, type ReactNode } from "react";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";

/**
 * The shared modal shell.
 *
 * The app grew 84 full-screen overlays against 8 files carrying
 * `role="dialog"`. An overlay without that role is announced as an anonymous
 * group, so a screen reader gets no signal that a dialog opened; and without a
 * focus trap, Tab walks straight out of the panel into the page underneath,
 * which is still fully focusable. Together that lets someone type into a form
 * they cannot see, behind a dialog they were never told about.
 *
 * Everything this needs already existed as hooks. What was missing was one
 * place to put them, so each overlay did not have to remember all four.
 *
 * NOT for progress modals. `TerraformProgressModal` and `DeployProgressModal`
 * report the state of something already running rather than asking for a
 * decision; they want a status region, not a focus trap that trades the user's
 * keyboard for a dialog they cannot answer.
 */
export interface ModalProps {
  open: boolean;
  /** Names the dialog. Rendered as the heading unless `hideTitle` is set. */
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Footer actions, laid out right-aligned below the body. */
  footer?: ReactNode;
  /**
   * Escape and backdrop click. Turn OFF only when interrupting would leave
   * something half-done (a running deploy, an upload in flight). A dialog that
   * cannot be dismissed still traps focus, so it must offer its own way out.
   */
  dismissible?: boolean;
  /** Tailwind max-width for the panel. */
  size?: "sm" | "md" | "lg" | "xl";
  /** Keep the title as the accessible name without drawing a heading. */
  hideTitle?: boolean;
}

/** How many modals currently want the page behind them to stay still. */
let scrollLocks = 0;
let overflowBeforeLock = "";

const SIZES = {
  sm: "max-w-md",
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
} as const;

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  dismissible = true,
  size = "md",
  hideTitle = false,
}: ModalProps) {
  const titleId = useId();
  const trapRef = useFocusTrap<HTMLDivElement>(open);

  useDismissOnEscape(open && dismissible, onClose);

  // A modal that scrolls the page behind it reads as two documents at once, and
  // on touch the background is what actually moves.
  //
  // Counted rather than saved-and-restored per dialog: a dialog opened from
  // inside another would capture "hidden" as the value to put back, so whichever
  // one unmounted last decided the outcome and the page could stay locked with
  // nothing on screen. The style is touched only on the first lock and the last
  // release.
  useEffect(() => {
    if (!open) return;
    if (scrollLocks === 0) {
      overflowBeforeLock = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    scrollLocks++;
    return () => {
      scrollLocks--;
      if (scrollLocks === 0) document.body.style.overflow = overflowBeforeLock;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      data-testid="modal-backdrop"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={dismissible ? onClose : undefined}
    >
      <div
        ref={trapRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        // The backdrop closes on click, so the panel has to stop the event or
        // every click inside the dialog would dismiss it.
        onClick={(e) => e.stopPropagation()}
        className={`flex max-h-[85vh] w-full ${SIZES[size]} flex-col rounded-lg bg-white shadow-xl`}
      >
        <div
          className={
            hideTitle
              ? "sr-only"
              : "flex items-start justify-between gap-4 border-b border-gray-200 px-6 py-4"
          }
        >
          <h2 id={titleId} className={hideTitle ? "" : "text-lg font-semibold text-gray-900"}>
            {title}
          </h2>
          {/* Named, not a bare glyph. Every overlay this replaced hand-rolled a
              naked &times; with no accessible name, which is how they ended up
              in the unnamed-button count. Omitted when the dialog cannot be
              dismissed, because there it would promise an exit that does not
              exist. */}
          {dismissible && !hideTitle && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="-mr-1 shrink-0 rounded px-1 text-xl leading-none text-gray-500 hover:text-gray-700"
            >
              &times;
            </button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
        {footer && (
          <div className="flex justify-end gap-3 border-t border-gray-200 px-6 py-4">{footer}</div>
        )}
      </div>
    </div>
  );
}
