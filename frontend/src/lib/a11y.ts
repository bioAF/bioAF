import type { KeyboardEvent } from "react";

/**
 * Enter/Space activation for an element that is clickable but not natively
 * focusable.
 *
 * A plain `onClick` on a <div>, <tr> or <li> is mouse-only: those elements are
 * not in the tab order and have no built-in key behaviour, so there is no
 * sequence a keyboard user can press to reach them. Every helper below pairs
 * the existing click handler with this, plus `tabIndex`, so the element becomes
 * reachable and operable.
 */
function activateOnKey<T extends HTMLElement>(activate: () => void) {
  return (e: KeyboardEvent<T>) => {
    // Only when the element ITSELF is focused. These containers routinely hold
    // their own buttons and checkboxes, and keydown bubbles: without this,
    // pressing Enter on a row's Delete button would also fire the row action.
    if (e.target !== e.currentTarget) return;
    if (e.key !== "Enter" && e.key !== " ") return;
    // Space scrolls the page and Enter can submit a surrounding form.
    e.preventDefault();
    activate();
  };
}

/**
 * For a clickable <tr> or a sortable <th>.
 *
 * Deliberately sets no `role`. A `role="button"` on a <tr> removes it from the
 * table's accessibility tree, which would undo the `scope` association work and
 * leave the table announced as a flat list of values. The row stays a row and
 * simply becomes focusable and operable.
 */
export function clickableRow(activate: () => void) {
  return {
    onClick: activate,
    // Typed against HTMLElement rather than HTMLTableRowElement so the same
    // helper fits a <th>. Handler parameters are contravariant, so one that
    // accepts the wider type is assignable wherever the narrower is expected.
    onKeyDown: activateOnKey<HTMLElement>(activate),
    tabIndex: 0,
  };
}

/**
 * For a clickable <div>, <li> or <img> acting as a card or list entry.
 *
 * Unlike a row, these carry no semantics at all, so they do get `role="button"`
 * and are announced as the controls they already behave like.
 *
 * Pass `label` when the visible content does not name the action on its own.
 */
export function clickableCard(activate: () => void, label?: string) {
  return {
    onClick: activate,
    onKeyDown: activateOnKey<HTMLElement>(activate),
    tabIndex: 0,
    role: "button",
    ...(label === undefined ? {} : { "aria-label": label }),
  };
}
