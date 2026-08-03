import { render, screen, fireEvent } from "@testing-library/react";
import { ConfirmDialog } from "./ConfirmDialog";

function setup(props: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onConfirm = jest.fn();
  const onCancel = jest.fn();
  render(
    <ConfirmDialog
      open
      title="Send reset"
      message="Send a password reset email?"
      confirmLabel="Send"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  );
  return { onConfirm, onCancel };
}

test("confirm fires the handler when not busy", () => {
  const { onConfirm } = setup();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(onConfirm).toHaveBeenCalledTimes(1);
});

test("shows a working state and disables the confirm button while busy", () => {
  setup({ busy: true });
  const confirm = screen.getByRole("button", { name: /working/i });
  expect(confirm).toBeDisabled();
});

test("does not fire confirm again while busy (guards double submit)", () => {
  const { onConfirm } = setup({ busy: true });
  fireEvent.click(screen.getByRole("button", { name: /working/i }));
  expect(onConfirm).not.toHaveBeenCalled();
});

test("disables cancel while busy", () => {
  setup({ busy: true });
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
});

// Accessibility. This is the styled replacement for window.confirm(), and the review's heuristic-3
// complaint was that a native confirm cannot be Escaped. A replacement that also cannot be Escaped,
// and that announces as an anonymous div, is worse than what it replaced on both counts.

test("is a real dialog, named by its title", () => {
  setup();
  const dialog = screen.getByRole("dialog");
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(dialog).toHaveAccessibleName("Send reset");
});

test("Escape cancels", () => {
  const { onCancel } = setup();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(onCancel).toHaveBeenCalledTimes(1);
});

test("Escape does not cancel while the action is in flight", () => {
  const { onCancel } = setup({ busy: true });
  fireEvent.keyDown(document, { key: "Escape" });
  expect(onCancel).not.toHaveBeenCalled();
});

test("moves focus into the dialog, onto Cancel rather than the destructive action", () => {
  setup({ variant: "danger", confirmLabel: "Delete" });
  expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
});

test("does not listen for Escape while closed", () => {
  const onCancel = jest.fn();
  render(
    <ConfirmDialog
      open={false}
      title="Send reset"
      message="Send a password reset email?"
      onConfirm={jest.fn()}
      onCancel={onCancel}
    />,
  );
  fireEvent.keyDown(document, { key: "Escape" });
  expect(onCancel).not.toHaveBeenCalled();
});
