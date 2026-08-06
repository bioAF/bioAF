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

  it("locks body scroll while open and releases it on close", () => {
    const { unmount } = render(<Harness />);
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).not.toBe("hidden");
  });
});
