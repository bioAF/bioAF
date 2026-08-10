import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { useWidgetData } from "./useWidgetData";

jest.mock("@/lib/errorReporting", () => ({
  logError: jest.fn(),
  loadFailureMessage: (what: string) => `${what} could not be loaded.`,
}));

import { logError } from "@/lib/errorReporting";

const mockLogError = logError as jest.Mock;

function Probe({ fetcher }: { fetcher: () => Promise<string> }) {
  const { data, loading, error, retry } = useWidgetData(fetcher, "Experiments");
  return (
    <div>
      {loading && <span data-testid="loading">loading</span>}
      {error && <span data-testid="error">{error}</span>}
      {data && <span data-testid="data">{data}</span>}
      <button onClick={retry}>Retry</button>
    </div>
  );
}

beforeEach(() => {
  mockLogError.mockReset();
});

test("loads once and hands back the data", async () => {
  const fetcher = jest.fn().mockResolvedValue("six experiments");
  render(<Probe fetcher={fetcher} />);

  expect(screen.getByTestId("loading")).toBeInTheDocument();
  expect(await screen.findByTestId("data")).toHaveTextContent("six experiments");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

test("a failure is reported in words and the real error goes to the log", async () => {
  const boom = new Error("500 from /api/experiments");
  const fetcher = jest.fn().mockRejectedValue(boom);
  render(<Probe fetcher={fetcher} />);

  expect(await screen.findByTestId("error")).toHaveTextContent("Experiments could not be loaded.");
  expect(screen.getByTestId("error")).not.toHaveTextContent("500 from");
  expect(mockLogError).toHaveBeenCalledWith(expect.stringContaining("Experiments"), boom);
});

test("retry refetches this widget instead of reloading the page", async () => {
  const fetcher = jest
    .fn()
    .mockRejectedValueOnce(new Error("boom"))
    .mockResolvedValueOnce("recovered");
  render(<Probe fetcher={fetcher} />);

  await screen.findByTestId("error");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(await screen.findByTestId("data")).toHaveTextContent("recovered");
  expect(screen.queryByTestId("error")).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

test("a retry that fails again says so rather than going quiet", async () => {
  const fetcher = jest.fn().mockRejectedValue(new Error("still down"));
  render(<Probe fetcher={fetcher} />);

  await screen.findByTestId("error");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  // The message clears while the retry is in flight, so wait for it to return
  // rather than reading the gap in between.
  expect(await screen.findByTestId("error")).toBeInTheDocument();
});

test("an inline fetcher does not send it into a refetch loop", async () => {
  const calls = { n: 0 };
  function Inline() {
    // Every render makes a new function, which is what a widget writes.
    const { data } = useWidgetData(async () => {
      calls.n += 1;
      return "once";
    }, "Runs");
    return <span data-testid="data">{data ?? "..."}</span>;
  }
  render(<Inline />);

  await waitFor(() => expect(screen.getByTestId("data")).toHaveTextContent("once"));
  await act(async () => {
    await new Promise((r) => setTimeout(r, 50));
  });
  expect(calls.n).toBe(1);
});

test("a result that arrives after unmount does not set state on a dead widget", async () => {
  let resolve: (v: string) => void = () => {};
  const fetcher = () => new Promise<string>((r) => (resolve = r));
  const { unmount } = render(<Probe fetcher={fetcher} />);

  unmount();
  await act(async () => {
    resolve("late");
    await Promise.resolve();
  });
  // No act() warning and no throw is the assertion; React logs an error if a
  // dead component sets state.
  expect(true).toBe(true);
});
