import { useEffect, useRef } from "react";

// Everything that can hold focus. `[tabindex="-1"]` is excluded on purpose: it
// is programmatically focusable but deliberately outside the tab sequence, and
// it is what the dialog container itself uses as a fallback target.
const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * Keep keyboard focus inside an open dialog, and give it back when it closes.
 *
 * A dialog that only *looks* modal still leaves the page behind it in the tab
 * order, so tab walks out from under the overlay and the user ends up operating
 * controls they cannot see. Escape alone does not fix that.
 *
 * Attach the returned ref to the dialog container and give it `tabIndex={-1}`
 * so it can take focus when it holds nothing focusable of its own.
 */
export function useFocusTrap<T extends HTMLElement>(open: boolean) {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    if (!open) return;
    const container = ref.current;
    if (!container) return;

    // Whatever the user was on when the dialog opened. Captured before focus
    // moves, so it is the real opener rather than something inside the dialog.
    const opener = document.activeElement as HTMLElement | null;

    // Re-queried on every keypress: dialogs load content, disable buttons while
    // busy, and reveal fields, so a list captured at open goes stale.
    const focusable = () =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) =>
          !el.hasAttribute("hidden") &&
          el.getAttribute("type") !== "hidden" &&
          el.getAttribute("aria-hidden") !== "true",
      );

    (focusable()[0] ?? container).focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) {
        // Nothing to move to, so leaving would be a one-way trip out.
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      // Only intervene at the two edges. Anywhere in between, the browser's own
      // tab order is already right and overriding it would break it.
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    container.addEventListener("keydown", onKeyDown);
    return () => {
      container.removeEventListener("keydown", onKeyDown);
      // Hand focus back, so a keyboard user resumes where they were instead of
      // at the top of the document.
      if (opener && document.contains(opener)) opener.focus();
    };
  }, [open]);

  return ref;
}
