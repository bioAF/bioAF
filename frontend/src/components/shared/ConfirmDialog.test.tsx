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
