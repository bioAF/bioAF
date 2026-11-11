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

/**
 * Busy -> failed -> retry: Tab recovered focus to the container while every
 * control was disabled, the request failed and re-enabled them with the dialog
 * still open, and Shift+Tab from the container went to the page behind it.
 */
describe("focus on the dialog container once controls are back", () => {
  function setup() {
    const view = render(
      <>
        <button type="button">Page behind</button>
        <InputDialog open busy title="Dismiss" label="Reason" onConfirm={() => {}} onCancel={() => {}} />
        <button type="button">Page after</button>
      </>,
    );
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(document.body, { key: "Tab" });
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveFocus();
    view.rerender(
      <>
        <button type="button">Page behind</button>
        <InputDialog open busy={false} error="The request failed." title="Dismiss" label="Reason" onConfirm={() => {}} onCancel={() => {}} />
        <button type="button">Page after</button>
      </>,
    );
    expect(dialog).toHaveFocus();
    return dialog;
  }

  test("Shift+Tab goes to the dialog's last control, not the page behind", () => {
    const dialog = setup();
    // Dispatched rather than userEvent.tab(): user-event moves focus by its own
    // rules after the handlers run, so it cannot show what the trap decided.
    // Chromium behavior is in browser-evidence.md.
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
    // Confirm stays disabled until a reason is typed, so Cancel is the last control.
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  test("Tab goes to the dialog's first control", () => {
    const dialog = setup();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(screen.getByLabelText("Reason")).toHaveFocus();
  });

  test("with everything still disabled, Tab and Shift+Tab stay on the container", () => {
    render(
      <>
        <button type="button">Page behind</button>
        <InputDialog open busy title="Dismiss" label="Reason" onConfirm={() => {}} onCancel={() => {}} />
      </>,
    );
    const dialog = screen.getByRole("dialog");
    dialog.focus();
    expect(fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true })).toBe(false);
    expect(dialog).toHaveFocus();
    expect(fireEvent.keyDown(dialog, { key: "Tab" })).toBe(false);
    expect(dialog).toHaveFocus();
  });
});
