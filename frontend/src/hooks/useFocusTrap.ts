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
/**
 * Every trap currently mounted, oldest first. Only the last one is allowed to
 * act.
 *
 * A dialog opened from inside another dialog renders as a DOM descendant of it,
 * which breaks a lone trap two ways. React runs child effects before parent
 * effects, so when both mount in the same commit the OUTER trap initialises last
 * and pulls focus straight back out of the dialog that just opened. And because
 * the inner panel is nested, a Tab raised inside it bubbles into the outer
 * container's listener as well, where the outer's focusable list (which contains
 * the inner's controls) disagrees about which element is "last".
 */
const liveTraps: HTMLElement[] = [];

/** Everything inside `container` that can actually take focus right now. */
function focusableIn(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) =>
      !el.hasAttribute("hidden") &&
      el.getAttribute("type") !== "hidden" &&
      el.getAttribute("aria-hidden") !== "true",
  );
}

/**
 * Which trap owns the keyboard right now.
 *
 * Registration order cannot answer this: React runs child effects before parent
 * effects, so a nested dialog registers BEFORE the one it opened from. Nesting
 * is therefore read off the DOM. A trap is topmost when nothing deeper is also
 * trapping, and among traps that are not nested in each other (a page dialog and
 * a confirm from the root provider, say) the most recently opened one wins.
 */
function isTopmostTrap(container: HTMLElement): boolean {
  const deeper = liveTraps.some((c) => c !== container && container.contains(c));
  if (deeper) return false;
  const candidates = liveTraps.filter(
    (c) => !liveTraps.some((other) => other !== c && c.contains(other)),
  );
  return candidates[candidates.length - 1] === container;
}

export function useFocusTrap<T extends HTMLElement>(open: boolean) {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    if (!open) return;
    const container = ref.current;
    if (!container) return;

    liveTraps.push(container);
    const isTopmost = () => isTopmostTrap(container);

    // Whatever the user was on when the dialog opened. Captured before focus
    // moves, so it is the real opener rather than something inside the dialog.
    const opener = document.activeElement as HTMLElement | null;

    // Re-queried on every keypress: dialogs load content, disable buttons while
    // busy, and reveal fields, so a list captured at open goes stale.
    const focusable = () => focusableIn(container);

    // Only claim focus if nothing deeper already has it. Without this the outer
    // dialog of a nested pair steals it back the moment the inner one opens.
    if (isTopmost()) (focusable()[0] ?? container).focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      // A nested dialog owns the keyboard while it is open. This listener still
      // receives the event by bubbling, and acting on it would fight the inner
      // trap over where focus lands.
      if (!isTopmost()) return;
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
      const i = liveTraps.indexOf(container);
      if (i !== -1) liveTraps.splice(i, 1);

      // Hand focus back, so a keyboard user resumes where they were instead of
      // at the top of the document.
      if (opener && opener !== document.body && document.contains(opener)) {
        opener.focus();
        return;
      }
      // No usable opener. That is the normal case for a dialog that opened in
      // the same commit as the one beneath it: child effects run first, so at
      // the time this trap captured its opener nothing had been focused yet and
      // it recorded the body. Falling back to the body would drop a keyboard
      // user out of the dialog that is still on screen, so focus goes to
      // whichever trap is now on top.
      const next = liveTraps.filter(
        (c) => !liveTraps.some((other) => other !== c && c.contains(other)),
      ).pop();
      if (next) (focusableIn(next)[0] ?? next).focus();
    };
  }, [open]);

  return ref;
}
