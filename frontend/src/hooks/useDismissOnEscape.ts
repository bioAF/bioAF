import { useEffect, useRef, type RefObject } from "react";

/**
 * The open dialogs, oldest first. A dialog opened later sits on top.
 *
 * Escape is a global key with a local meaning, so "which dialog does it close?"
 * cannot be answered by a listener alone: every open dialog hears the same
 * event. Modals in this app nest (QCReportModal renders a PlotModal), and the
 * hand-rolled handlers this hook replaces each listened on `document`
 * independently, so Escape closed the plot AND the report underneath it.
 */
interface OpenDialog {
  id: symbol;
  container?: RefObject<HTMLElement | null>;
}

const openDialogs: OpenDialog[] = [];

/**
 * Which open dialog Escape belongs to.
 *
 * Registration order alone is wrong when an outer dialog and one nested in it
 * open in the same commit: React runs the child's effects first, so the outer
 * registers last and would look topmost. As in useFocusTrap, nesting is read
 * off the DOM: a dialog whose container holds another open dialog is never on
 * top. Among the rest (dialogs side by side, or ones registered without a
 * container), the most recently opened wins.
 */
function topmost(): symbol | undefined {
  const candidates = openDialogs.filter((d) => {
    const el = d.container?.current;
    if (!el) return true;
    return !openDialogs.some((other) => {
      const inner = other.container?.current;
      return other !== d && !!inner && inner !== el && el.contains(inner);
    });
  });
  return candidates[candidates.length - 1]?.id;
}

/**
 * Close a dialog when the user presses Escape, but only if it is the topmost
 * one.
 *
 * Call it unconditionally, as the rules of hooks require, and pass `open` for
 * whether it should currently be listening. `onDismiss` may be an inline arrow:
 * it is read through a ref, so a changing callback identity does not
 * re-register the listener or reorder the stack.
 *
 * `canDismiss` false keeps the dialog on the stack but refuses the Escape: a
 * busy confirm or a dialog that cannot be interrupted is still the one on top,
 * so the dialog underneath must not close in its place.
 *
 * `container` is the dialog's own element. Pass it for anything that can be
 * nested in, or contain, another dialog, so nesting decides which is on top.
 */
export function useDismissOnEscape(
  open: boolean,
  onDismiss: () => void,
  canDismiss: boolean = true,
  container?: RefObject<HTMLElement | null>,
) {
  const latest = useRef(onDismiss);
  const allowed = useRef(canDismiss);

  // Keep the refs current without making them effect dependencies below.
  useEffect(() => {
    latest.current = onDismiss;
    allowed.current = canDismiss;
  });

  useEffect(() => {
    if (!open) return;

    const id = Symbol("dialog");
    const entry: OpenDialog = { id, container };
    openDialogs.push(entry);

    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Ignore the event unless this dialog is the one on top.
      if (topmost() !== id) return;
      e.preventDefault();
      if (allowed.current) latest.current();
    };

    // `window`, not `document`. A real keypress bubbles from the focused
    // element through document to window, so either would catch it, but an
    // event dispatched directly at `document` still reaches window while one
    // dispatched at `window` never reaches document. Listening higher up costs
    // nothing and misses nothing.
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      const i = openDialogs.indexOf(entry);
      if (i !== -1) openDialogs.splice(i, 1);
    };
  }, [open, container]);
}
