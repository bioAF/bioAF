import { fireEvent, render, screen, within } from "@/testing/renderWithProviders";
import { DestroyStorageDialog, TeardownDialog } from "./StackActionDialogs";

const confirmButton = (name: RegExp) =>
  within(screen.getByRole("dialog"))
    .getAllByRole("button")
    .find((b) => name.test(b.textContent ?? "") && b.className.includes("bg-red"))!;

test("destroying storage needs the checkbox and the typed phrase, and both reset when reopened", () => {
  const onConfirm = jest.fn();
  const props = { storageLabel: "GCS", onCancel: () => {}, onConfirm };
  const view = render(<DestroyStorageDialog open {...props} />);
  const destroy = confirmButton(/Destroy Storage/);
  expect(destroy).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(destroy).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/to confirm/), { target: { value: "delete my data" } });
  expect(destroy).toBeEnabled();
  fireEvent.click(destroy);
  expect(onConfirm).toHaveBeenCalledTimes(1);

  view.rerender(<DestroyStorageDialog open={false} {...props} />);
  view.rerender(<DestroyStorageDialog open {...props} />);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByLabelText(/to confirm/)).toHaveValue("");
});

test("a reason keeps teardown disabled even with consent given", () => {
  render(<TeardownDialog open reason="Your role does not include infrastructure configure permission." onCancel={() => {}} onConfirm={() => {}} />);
  fireEvent.click(screen.getByRole("checkbox"));
  const teardown = confirmButton(/Teardown/);
  expect(teardown).toBeDisabled();
  expect(teardown).toHaveAccessibleDescription(/configure permission/);
});
