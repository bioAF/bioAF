import { render, fireEvent, screen } from "@testing-library/react";
import { useFocusTrap } from "./useFocusTrap";

function Dialog({ open }: { open: boolean }) {
  const ref = useFocusTrap<HTMLDivElement>(open);
  return (
    <>
      <button>outside before</button>
      {open && (
        <div ref={ref} tabIndex={-1} role="dialog" aria-label="test">
          <button>first</button>
          <input aria-label="middle" />
          <button>last</button>
        </div>
      )}
      <button>outside after</button>
    </>
  );
}

const tab = (shift = false) =>
  fireEvent.keyDown(document.activeElement!, { key: "Tab", shiftKey: shift });

test("focus moves into the dialog when it opens", () => {
  render(<Dialog open />);

  expect(screen.getByText("first")).toHaveFocus();
});

test("Tab past the last control wraps to the first, not out to the page", () => {
  // Without this, tab walks out of the dialog and into the page behind it,
  // which is still rendered. The user is then operating controls they cannot
  // see, underneath an overlay.
  render(<Dialog open />);
  screen.getByText("last").focus();

  tab();

  expect(screen.getByText("first")).toHaveFocus();
});

test("Shift+Tab before the first control wraps to the last", () => {
  render(<Dialog open />);
  screen.getByText("first").focus();

  tab(true);

  expect(screen.getByText("last")).toHaveFocus();
});

test("Tab between controls inside the dialog is left alone", () => {
  // The trap only intervenes at the two edges. Anywhere else the browser's own
  // tab order is correct and interfering would break it.
  render(<Dialog open />);
  screen.getByText("first").focus();

  const evt = fireEvent.keyDown(screen.getByText("first"), { key: "Tab" });

  expect(evt).toBe(true); // not preventDefault'ed
});

test("focus returns to whatever opened the dialog when it closes", () => {
  // Otherwise focus resets to the top of the document and a keyboard user has
  // to tab all the way back to where they were.
  const { rerender } = render(<Dialog open={false} />);
  const opener = screen.getByText("outside before");
  opener.focus();

  rerender(<Dialog open />);
  expect(screen.getByText("first")).toHaveFocus();

  rerender(<Dialog open={false} />);
  expect(opener).toHaveFocus();
});

test("a dialog with no focusable content still takes focus itself", () => {
  // Otherwise focus stays on the page behind, and Escape and the trap both have
  // nothing to act on.
  function Empty() {
    const ref = useFocusTrap<HTMLDivElement>(true);
    return (
      <div ref={ref} tabIndex={-1} role="dialog" aria-label="empty">
        <p>Loading...</p>
      </div>
    );
  }
  render(<Empty />);

  expect(screen.getByRole("dialog")).toHaveFocus();
});

test("hidden controls are not counted as the edges of the trap", () => {
  // A `hidden` input inside a dialog would otherwise become the wrap target and
  // focus would appear to vanish.
  function WithHidden() {
    const ref = useFocusTrap<HTMLDivElement>(true);
    return (
      <div ref={ref} tabIndex={-1} role="dialog" aria-label="h">
        <input type="hidden" />
        <button>only</button>
      </div>
    );
  }
  render(<WithHidden />);

  expect(screen.getByText("only")).toHaveFocus();
});
