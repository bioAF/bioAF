import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { ConfirmProvider, useConfirm } from "./useConfirm";

/**
 * The contract that matters is that this is a DROP-IN for window.confirm():
 * it returns a promise of a boolean, so `if (!(await confirm(...))) return;`
 * keeps the exact control flow the native call had. Everything below asserts
 * that equivalence rather than the dialog's markup, which ConfirmDialog already
 * has its own tests for.
 */

function Harness({ opts, onResult }: { opts?: object; onResult: (v: boolean) => void }) {
  const confirm = useConfirm();
  return (
    <button
      onClick={async () => {
        const ok = await confirm({
          title: "Delete it?",
          message: "This cannot be undone.",
          confirmLabel: "Delete",
          ...opts,
        });
        onResult(ok);
      }}
    >
      Trigger
    </button>
  );
}

function renderHarness(opts?: object) {
  const onResult = jest.fn();
  render(
    <ConfirmProvider>
      <Harness opts={opts} onResult={onResult} />
    </ConfirmProvider>
  );
  return onResult;
}

async function open() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Trigger" }));
  });
}

describe("useConfirm", () => {
  it("shows nothing until it is called", () => {
    renderHarness();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("resolves true when confirmed", async () => {
    const onResult = renderHarness();
    await open();
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    });
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
  });

  it("resolves false when cancelled", async () => {
    const onResult = renderHarness();
    await open();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    });
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });

  it("resolves false on Escape, like a native confirm", async () => {
    const onResult = renderHarness();
    await open();
    await act(async () => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(false));
  });

  it("closes the dialog once it resolves", async () => {
    renderHarness();
    await open();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("renders the title, message and labels it was given", async () => {
    renderHarness({ confirmLabel: "Stop it", cancelLabel: "Keep it" });
    await open();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Delete it?");
    expect(dialog).toHaveTextContent("This cannot be undone.");
    expect(screen.getByRole("button", { name: "Stop it" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep it" })).toBeInTheDocument();
  });

  it("never leaves a caller awaiting forever when a second confirm opens", async () => {
    // A pending promise that never settles would hang the calling handler and
    // leave its busy state stuck on. If one confirm supersedes another, the
    // first must resolve false rather than be dropped.
    const results: boolean[] = [];
    function Two() {
      const confirm = useConfirm();
      return (
        <>
          <button
            onClick={async () => results.push(await confirm({ title: "First", message: "a" }))}
          >
            one
          </button>
          <button
            onClick={async () => results.push(await confirm({ title: "Second", message: "b" }))}
          >
            two
          </button>
        </>
      );
    }
    render(
      <ConfirmProvider>
        <Two />
      </ConfirmProvider>
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "one" }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "two" }));
    });
    await waitFor(() => expect(results).toContain(false));
    expect(screen.getByRole("dialog")).toHaveTextContent("Second");
  });

  it("throws outside a provider rather than silently doing nothing", () => {
    // A no-op fallback would make a destructive action fire with no gate at all,
    // which is worse than crashing in development.
    const quiet = jest.spyOn(console, "error").mockImplementation(() => {});
    function Bare() {
      useConfirm();
      return null;
    }
    expect(() => render(<Bare />)).toThrow(/ConfirmProvider/);
    quiet.mockRestore();
  });
});
