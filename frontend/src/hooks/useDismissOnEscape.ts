import { useEffect, useRef } from "react";

/**
 * The open dialogs, oldest first. A dialog opened later sits on top.
 *
 * Escape is a global key with a local meaning, so "which dialog does it close?"
 * cannot be answered by a listener alone: every open dialog hears the same
 * event. Modals in this app nest (QCReportModal renders a PlotModal), and the
 * hand-rolled handlers this hook replaces each listened on `document`
 * independently, so Escape closed the plot AND the report underneath it.
 */
const openDialogs: symbol[] = [];

/**
 * Close a dialog when the user presses Escape, but only if it is the topmost
 * one.
 *
 * Call it unconditionally, as the rules of hooks require, and pass `open` for
 * whether it should currently be listening. `onDismiss` may be an inline arrow:
 * it is read through a ref, so a changing callback identity does not
 * re-register the listener or reorder the stack.
 */
export function useDismissOnEscape(open: boolean, onDismiss: () => void) {
  const latest = useRef(onDismiss);

  // Keep the ref current without making it an effect dependency below.
  useEffect(() => {
    latest.current = onDismiss;
  });

  useEffect(() => {
    if (!open) return;

    const id = Symbol("dialog");
    openDialogs.push(id);

    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Ignore the event unless this dialog is the one on top.
      if (openDialogs[openDialogs.length - 1] !== id) return;
      latest.current();
    };

    // `window`, not `document`. A real keypress bubbles from the focused
    // element through document to window, so either would catch it, but an
    // event dispatched directly at `document` still reaches window while one
    // dispatched at `window` never reaches document. Listening higher up costs
    // nothing and misses nothing.
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      const i = openDialogs.indexOf(id);
      if (i !== -1) openDialogs.splice(i, 1);
    };
  }, [open]);
}
