import { render, screen, fireEvent } from "@testing-library/react";
import { InputDialog } from "./InputDialog";

function setup(props: Partial<React.ComponentProps<typeof InputDialog>> = {}) {
  const onConfirm = jest.fn();
  const onCancel = jest.fn();
  render(
    <InputDialog
      open
      title="Set API key"
      label="API key"
      confirmLabel="Save"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  );
  return { onConfirm, onCancel };
}

test("renders nothing when closed", () => {
  setup({ open: false });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("is a labelled modal dialog", () => {
  setup();
  const dialog = screen.getByRole("dialog");
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(dialog).toHaveAccessibleName("Set API key");
  expect(screen.getByLabelText("API key")).toBeInTheDocument();
});

test("confirms with the typed value", () => {
  const { onConfirm } = setup();
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-123" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onConfirm).toHaveBeenCalledWith("sk-123");
});

test("blocks an empty submit by default", () => {
  const { onConfirm } = setup();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onConfirm).not.toHaveBeenCalled();
});

test("allows an empty submit when allowEmpty (e.g. clearing a key)", () => {
  const { onConfirm } = setup({ allowEmpty: true });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onConfirm).toHaveBeenCalledWith("");
});

test("masks the value when type is password", () => {
  setup({ type: "password" });
  expect(screen.getByLabelText("API key")).toHaveAttribute("type", "password");
});

test("Enter submits, Escape cancels", () => {
  const { onConfirm, onCancel } = setup();
  const input = screen.getByLabelText("API key");
  fireEvent.change(input, { target: { value: "x" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onConfirm).toHaveBeenCalledWith("x");
  fireEvent.keyDown(input, { key: "Escape" });
  expect(onCancel).toHaveBeenCalled();
});

test("shows an error and a busy state", () => {
  setup({ error: "Key rejected", busy: true });
  expect(screen.getByText("Key rejected")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /saving|working/i })).toBeDisabled();
});

test("seeds the field from initialValue", () => {
  setup({ initialValue: "seed", allowEmpty: true });
  expect(screen.getByLabelText("API key")).toHaveValue("seed");
});
