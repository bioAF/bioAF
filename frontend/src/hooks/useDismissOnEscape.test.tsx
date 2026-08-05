import { render, fireEvent } from "@testing-library/react";
import { useDismissOnEscape } from "./useDismissOnEscape";

function Dialog({
  open,
  onDismiss,
  label = "dialog",
}: {
  open: boolean;
  onDismiss: () => void;
  label?: string;
}) {
  useDismissOnEscape(open, onDismiss);
  return open ? <div>{label}</div> : null;
}

const escape = () => fireEvent.keyDown(document, { key: "Escape" });

test("Escape dismisses an open dialog", () => {
  const onDismiss = jest.fn();
  render(<Dialog open onDismiss={onDismiss} />);

  escape();

  expect(onDismiss).toHaveBeenCalledTimes(1);
});

test("Escape does nothing while the dialog is closed", () => {
  // The hook has to be called unconditionally to satisfy the rules of hooks,
  // so "closed" cannot mean "not called". It has to mean "not listening", or
  // Escape would fire the close handler of every dialog on the page.
  const onDismiss = jest.fn();
  render(<Dialog open={false} onDismiss={onDismiss} />);

  escape();

  expect(onDismiss).not.toHaveBeenCalled();
});

test("other keys are left alone", () => {
  const onDismiss = jest.fn();
  render(<Dialog open onDismiss={onDismiss} />);

  for (const key of ["Enter", " ", "Tab", "a", "Esc"]) {
    fireEvent.keyDown(document, { key });
  }

  expect(onDismiss).not.toHaveBeenCalled();
});

test("the listener is torn down when the dialog unmounts", () => {
  const onDismiss = jest.fn();
  const { unmount } = render(<Dialog open onDismiss={onDismiss} />);

  unmount();
  escape();

  expect(onDismiss).not.toHaveBeenCalled();
});

test("closing the dialog stops it listening", () => {
  const onDismiss = jest.fn();
  const { rerender } = render(<Dialog open onDismiss={onDismiss} />);

  rerender(<Dialog open={false} onDismiss={onDismiss} />);
  escape();

  expect(onDismiss).not.toHaveBeenCalled();
});

test("Escape closes only the topmost dialog when two are stacked", () => {
  // This codebase really does nest: QCReportModal renders a PlotModal. Both of
  // the existing hand-rolled handlers listen on `document`, so Escape closed
  // BOTH at once, dumping the user out of the report they were reading rather
  // than out of the plot they opened from it.
  const closeOuter = jest.fn();
  const closeInner = jest.fn();
  render(
    <>
      <Dialog open onDismiss={closeOuter} label="outer" />
      <Dialog open onDismiss={closeInner} label="inner" />
    </>,
  );

  escape();

  expect(closeInner).toHaveBeenCalledTimes(1);
  expect(closeOuter).not.toHaveBeenCalled();
});

test("dismissing the top dialog hands control back to the one beneath", () => {
  const closeOuter = jest.fn();
  const closeInner = jest.fn();
  const { rerender } = render(
    <>
      <Dialog open onDismiss={closeOuter} label="outer" />
      <Dialog open onDismiss={closeInner} label="inner" />
    </>,
  );

  escape();
  rerender(
    <>
      <Dialog open onDismiss={closeOuter} label="outer" />
      <Dialog open={false} onDismiss={closeInner} label="inner" />
    </>,
  );
  escape();

  expect(closeInner).toHaveBeenCalledTimes(1);
  expect(closeOuter).toHaveBeenCalledTimes(1);
});

test("an inline callback does not disturb the stacking order", () => {
  // Call sites pass arrow functions, so the callback identity changes on every
  // render. If that re-registered the listener, a parent re-render would shove
  // an already-open background dialog to the top of the stack and Escape would
  // close the wrong one.
  const closeOuter = jest.fn();
  const closeInner = jest.fn();
  const Outer = ({ tick }: { tick: number }) => (
    <>
      <Dialog open onDismiss={() => closeOuter(tick)} label="outer" />
      <Dialog open onDismiss={() => closeInner(tick)} label="inner" />
    </>
  );
  const { rerender } = render(<Outer tick={1} />);

  rerender(<Outer tick={2} />);
  escape();

  expect(closeOuter).not.toHaveBeenCalled();
  // and the LATEST callback runs, not the one captured at mount
  expect(closeInner).toHaveBeenCalledWith(2);
});

test("catches the event whichever of window or document it is dispatched at", () => {
  // Not a stylistic choice. QCReportModal's own test dispatches at `window`,
  // and an event dispatched there never reaches `document`, so a
  // document-level listener silently misses it. Listening at `window` catches
  // both, since an event dispatched at `document` still propagates up to it.
  const onDismiss = jest.fn();
  render(<Dialog open onDismiss={onDismiss} />);

  fireEvent.keyDown(document, { key: "Escape" });
  expect(onDismiss).toHaveBeenCalledTimes(1);

  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  expect(onDismiss).toHaveBeenCalledTimes(2);
});
