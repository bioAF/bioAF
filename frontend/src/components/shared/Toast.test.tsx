// Exercise the real implementation, not the default test stub in jest.setup.ts.
jest.unmock("@/components/shared/Toast");

import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { ToastProvider, useToast } from "./Toast";

function Trigger({ run }: { run: (t: ReturnType<typeof useToast>) => void }) {
  const toast = useToast();
  return (
    <button onClick={() => run(toast)}>fire</button>
  );
}

const setup = (run: (t: ReturnType<typeof useToast>) => void) =>
  render(
    <ToastProvider>
      <Trigger run={run} />
    </ToastProvider>,
  );

test("the live regions exist before any toast is pushed", () => {
  // A live region has to be in the DOM before content lands in it. If the region
  // itself is inserted along with the message, a screen reader may see a new
  // node rather than a change to a watched one, and announce nothing. The
  // regions are therefore always mounted and start empty.
  render(
    <ToastProvider>
      <div>quiet</div>
    </ToastProvider>
  );
  const polite = document.querySelector('[aria-live="polite"]');
  const assertive = document.querySelector('[aria-live="assertive"]');
  expect(polite).toBeInTheDocument();
  expect(assertive).toBeInTheDocument();
  expect(polite).toBeEmptyDOMElement();
  expect(assertive).toBeEmptyDOMElement();
});

test("a pushed error lands inside the already-mounted assertive region", async () => {
  function Boom() {
    const toast = useToast();
    return <button onClick={() => toast.error("Disk on fire")}>boom</button>;
  }
  render(
    <ToastProvider>
      <Boom />
    </ToastProvider>
  );
  const assertive = document.querySelector('[aria-live="assertive"]')!;
  fireEvent.click(screen.getByRole("button", { name: "boom" }));
  await waitFor(() => expect(assertive).toHaveTextContent("Disk on fire"));
  // and not in the polite one
  expect(document.querySelector('[aria-live="polite"]')).not.toHaveTextContent("Disk on fire");
});

test("an error toast is announced assertively to screen readers", async () => {
  setup((t) => t.error("Could not stop the work node"));
  screen.getByText("fire").click();

  const region = await screen.findByRole("alert");
  expect(region).toHaveTextContent("Could not stop the work node");
});

test("a success toast is announced politely, not assertively", async () => {
  setup((t) => t.success("Settings saved"));
  screen.getByText("fire").click();

  const region = await screen.findByRole("status");
  expect(region).toHaveTextContent("Settings saved");
});

test("errors persist until dismissed, so a failure is never missed", async () => {
  jest.useFakeTimers();
  try {
    setup((t) => t.error("Rebuild failed"));
    screen.getByText("fire").click();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());

    act(() => {
      jest.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Rebuild failed");
  } finally {
    jest.useRealTimers();
  }
});

test("success toasts auto-dismiss so they do not pile up", async () => {
  jest.useFakeTimers();
  try {
    setup((t) => t.success("Saved"));
    screen.getByText("fire").click();
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());

    act(() => {
      jest.advanceTimersByTime(6000);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});

test("a toast can be dismissed by the user", async () => {
  setup((t) => t.error("Nope"));
  screen.getByText("fire").click();
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());

  screen.getByRole("button", { name: /dismiss/i }).click();
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

test("useToast outside a provider throws instead of silently doing nothing", () => {
  // A no-op fallback would recreate the exact bug this primitive exists to fix:
  // an error that goes nowhere.
  const spy = jest.spyOn(console, "error").mockImplementation(() => {});
  expect(() => render(<Trigger run={() => {}} />)).toThrow(/ToastProvider/);
  spy.mockRestore();
});
