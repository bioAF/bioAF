/**
 * Dialogs open on top of other dialogs: a confirm from inside a form modal, a
 * prompt from inside a detail modal. One Escape must dismiss only the dialog on
 * top, and a dialog that refuses dismissal (busy, or not dismissible) must not
 * let the Escape fall through to the one underneath.
 */
import { useState } from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal } from "./Modal";
import { ConfirmDialog } from "./ConfirmDialog";
import { InputDialog } from "./InputDialog";

function escape() {
  fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
}

type Inner = "confirm" | "input" | "modal";

function Harness({
  inner,
  innerBusy = false,
  innerDismissible = true,
  onOuterClose,
  onInnerCancel,
}: {
  inner: Inner;
  innerBusy?: boolean;
  innerDismissible?: boolean;
  onOuterClose: () => void;
  onInnerCancel: () => void;
}) {
  const [innerOpen, setInnerOpen] = useState(false);
  const cancel = () => {
    onInnerCancel();
    setInnerOpen(false);
  };
  return (
    <Modal open title="Outer" onClose={onOuterClose}>
      <button type="button" onClick={() => setInnerOpen(true)}>
        Open inner
      </button>
      {inner === "confirm" && (
        <ConfirmDialog open={innerOpen} title="Inner" message="Sure?" busy={innerBusy} onConfirm={() => {}} onCancel={cancel} />
      )}
      {inner === "input" && (
        <InputDialog open={innerOpen} title="Inner" label="Name" busy={innerBusy} onConfirm={() => {}} onCancel={cancel} />
      )}
      {inner === "modal" && (
        <Modal open={innerOpen} title="Inner" dismissible={innerDismissible} onClose={cancel}>
          <button type="button">Inside</button>
        </Modal>
      )}
    </Modal>
  );
}

async function openInner() {
  await userEvent.click(screen.getByRole("button", { name: "Open inner" }));
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe.each(["confirm", "input", "modal"] as const)("Escape with a %s on top of a Modal", (inner) => {
  test("dismisses only the inner dialog, then the outer on a second Escape", async () => {
    const outer = jest.fn();
    const innerCancel = jest.fn();
    render(<Harness inner={inner} onOuterClose={outer} onInnerCancel={innerCancel} />);
    await openInner();
    escape();
    expect(innerCancel).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    escape();
    expect(outer).toHaveBeenCalledTimes(1);
  });

  test("returns focus to the control that opened the inner dialog", async () => {
    render(<Harness inner={inner} onOuterClose={jest.fn()} onInnerCancel={jest.fn()} />);
    await openInner();
    expect(screen.getByRole("button", { name: "Open inner" })).not.toHaveFocus();
    escape();
    expect(screen.getByRole("button", { name: "Open inner" })).toHaveFocus();
  });
});

describe.each(["confirm", "input"] as const)("a busy %s on top of a Modal", (inner) => {
  test("Escape dismisses neither dialog", async () => {
    const outer = jest.fn();
    const innerCancel = jest.fn();
    render(<Harness inner={inner} innerBusy onOuterClose={outer} onInnerCancel={innerCancel} />);
    await openInner();
    escape();
    expect(innerCancel).not.toHaveBeenCalled();
    expect(outer).not.toHaveBeenCalled();
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
  });
});

test("a non-dismissible Modal on top keeps Escape from closing the one beneath", async () => {
  const outer = jest.fn();
  const innerCancel = jest.fn();
  render(<Harness inner="modal" innerDismissible={false} onOuterClose={outer} onInnerCancel={innerCancel} />);
  await openInner();
  escape();
  expect(innerCancel).not.toHaveBeenCalled();
  expect(outer).not.toHaveBeenCalled();
});

test("a standalone ConfirmDialog still cancels on Escape dispatched at the document", () => {
  const cancel = jest.fn();
  render(<ConfirmDialog open title="T" message="m" onConfirm={() => {}} onCancel={cancel} />);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(cancel).toHaveBeenCalledTimes(1);
});

test("a standalone InputDialog still cancels on Escape from its field", async () => {
  const cancel = jest.fn();
  render(<InputDialog open title="T" label="Name" onConfirm={() => {}} onCancel={cancel} />);
  fireEvent.keyDown(screen.getByLabelText("Name"), { key: "Escape" });
  expect(cancel).toHaveBeenCalledTimes(1);
});
