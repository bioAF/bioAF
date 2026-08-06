import { render, screen, fireEvent, act } from "@testing-library/react";
import { useState } from "react";
import { Modal } from "./Modal";

/**
 * The primitive exists because the app had 84 full-screen overlays against 8
 * files carrying role="dialog". These tests pin the four things a screen reader
 * and a keyboard user actually need, so a component converted onto Modal cannot
 * quietly lose them.
 */

function Harness({ onClose = () => {}, ...rest }: Partial<React.ComponentProps<typeof Modal>>) {
  return (
    <Modal open title="Edit sample" onClose={onClose} {...rest}>
      <input aria-label="First field" />
      <button>Second</button>
    </Modal>
  );
}

describe("Modal", () => {
  it("renders nothing when closed", () => {
    render(
      <Modal open={false} title="Hidden" onClose={() => {}}>
        <p>body</p>
      </Modal>
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("exposes itself as a modal dialog named by its title", () => {
    render(<Harness />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    // The accessible name has to come from the title element, not a guess.
    expect(dialog).toHaveAccessibleName("Edit sample");
  });

  it("closes on Escape", () => {
    const onClose = jest.fn();
    render(<Harness onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on a backdrop click but not on a click inside the panel", () => {
    const onClose = jest.fn();
    render(<Harness onClose={onClose} />);
    fireEvent.click(screen.getByTestId("modal-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);

    onClose.mockClear();
    fireEvent.click(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("moves focus into the dialog on open", () => {
    render(<Harness />);
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("keeps Tab inside the dialog instead of walking into the page behind", () => {
    render(
      <>
        <button>outside before</button>
        <Harness />
        <button>outside after</button>
      </>
    );
    const dialog = screen.getByRole("dialog");
    const focusables = dialog.querySelectorAll("input,button");
    const last = focusables[focusables.length - 1] as HTMLElement;

    last.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    // Wrapped back to the first control, rather than escaping to "outside after".
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("restores focus to whatever opened it on close", () => {
    function Toggle() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>opener</button>
          <Modal open={open} title="T" onClose={() => setOpen(false)}>
            <button>inside</button>
          </Modal>
        </>
      );
    }
    render(<Toggle />);
    const opener = screen.getByRole("button", { name: "opener" });
    act(() => {
      opener.focus();
      fireEvent.click(opener);
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    act(() => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(document.activeElement).toBe(opener);
  });

  it("can opt out of dismissal for an operation that must not be interrupted", () => {
    // A deploy in progress should not vanish because someone tapped the backdrop.
    const onClose = jest.fn();
    render(<Harness onClose={onClose} dismissible={false} />);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByTestId("modal-backdrop"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("offers a close control with a real name, not a bare glyph", () => {
    // The overlays this replaced each hand-rolled a naked &times; with no
    // accessible name, which is why they showed up in the unnamed-button count.
    const onClose = jest.fn();
    render(<Harness onClose={onClose} />);
    const close = screen.getByRole("button", { name: "Close" });
    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("omits the close control when the dialog is not dismissible", () => {
    // Such a dialog has its own way out; an X here would contradict it.
    render(<Harness dismissible={false} />);
    expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
  });

  it("hands the keyboard to a nested dialog and takes it back on close", () => {
    // Three places in the app open a second dialog from inside the first
    // (version picker, plot viewer, assembled prompt). The inner panel is a DOM
    // descendant of the outer one, so a Tab inside it bubbles into BOTH traps.
    // Only the topmost may act, or the two fight over where focus lands.
    function Nested({ inner }: { inner: boolean }) {
      return (
        <Modal open title="Outer" onClose={() => {}}>
          <button>outer control</button>
          {inner && (
            <Modal open title="Inner" onClose={() => {}}>
              <button>inner control</button>
            </Modal>
          )}
        </Modal>
      );
    }
    // Mounted together on purpose: child effects run before parent effects, so
    // this is the ordering where the OUTER trap runs last and would otherwise
    // yank focus back out of the dialog that just opened.
    const { rerender } = render(<Nested inner />);
    const dialogs = screen.getAllByRole("dialog");
    expect(dialogs).toHaveLength(2);
    const outer = dialogs[0];
    const innerPanel = dialogs[1];
    expect(outer.contains(innerPanel)).toBe(true);

    // Focus moved into the inner dialog, and tabbing keeps it there.
    expect(innerPanel.contains(document.activeElement)).toBe(true);
    for (let i = 0; i < 6; i++) fireEvent.keyDown(innerPanel, { key: "Tab" });
    expect(innerPanel.contains(document.activeElement)).toBe(true);

    // Close the inner one; the outer trap must resume rather than stay inert.
    rerender(<Nested inner={false} />);
    const outerAgain = screen.getByRole("dialog");
    for (let i = 0; i < 6; i++) fireEvent.keyDown(outerAgain, { key: "Tab" });
    expect(outerAgain.contains(document.activeElement)).toBe(true);
  });

  it("locks body scroll while open and releases it on close", () => {
    const { unmount } = render(<Harness />);
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).not.toBe("hidden");
  });
});
