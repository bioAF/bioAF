/**
 * A dialog that goes busy disables its own controls, and the browser drops
 * focus from a control as it becomes disabled. Focus then sits on <body>,
 * outside the dialog, where the trap's own listener never hears Tab, so Tab
 * walked the page behind the dialog. Reproduced in Chromium on the
 * literature paper page's busy dismiss prompt.
 */
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InputDialog } from "@/components/shared/InputDialog";
import { Modal } from "@/components/shared/Modal";

test("Tab from a lost focus returns into the open busy dialog, not the page behind", () => {
  render(
    <>
      <button type="button">Page behind</button>
      <InputDialog open busy title="Dismiss" label="Reason" onConfirm={() => {}} onCancel={() => {}} />
    </>,
  );
  (document.activeElement as HTMLElement | null)?.blur();
  expect(document.activeElement).toBe(document.body);
  fireEvent.keyDown(document.body, { key: "Tab" });
  const dialog = screen.getByRole("dialog");
  expect(dialog.contains(document.activeElement)).toBe(true);
  expect(screen.getByRole("button", { name: "Page behind" })).not.toHaveFocus();
});

test("with controls available, Tab from a lost focus lands on the dialog's first control", async () => {
  render(
    <>
      <button type="button">Page behind</button>
      <Modal open title="Edit" onClose={() => {}}>
        <input aria-label="Name" />
      </Modal>
    </>,
  );
  (document.activeElement as HTMLElement | null)?.blur();
  await userEvent.tab();
  expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true);
});

test("Tab inside the dialog is untouched (the trap still wraps at the edges)", async () => {
  render(
    <Modal open title="Edit" onClose={() => {}}>
      <input aria-label="Name" />
    </Modal>,
  );
  screen.getByLabelText("Name").focus();
  await userEvent.tab();
  expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
});

test("with no dialog open, Tab is not intercepted", async () => {
  render(<button type="button">Alone</button>);
  await userEvent.tab();
  expect(screen.getByRole("button", { name: "Alone" })).toHaveFocus();
});
