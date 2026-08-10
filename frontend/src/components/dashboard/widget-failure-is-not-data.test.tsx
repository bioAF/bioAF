/**
 * The four widgets that reported a failure as a real number.
 *
 * Measured on the deployed app 2026-08-07, all 18 widgets forced on screen and
 * every non-shell `/api/**` request answered 500. Fourteen widgets showed a plain
 * sentence and a working Retry. These four rendered text **byte-identical to
 * their healthy state**:
 *
 *   widget-running-jobs   "0 / 0 pending / View all runs"
 *   widget-queue-depth    "0 / pending jobs"
 *   widget-team-output    "0 runs completed / 0 experiments started"
 *   widget-my-sessions    "No active sessions."
 *
 * No pixel, no character and no ARIA attribute distinguished "the platform is
 * down" from "you have nothing queued". On a genomics platform a compute backlog
 * invisible during an outage reads as a healthy idle cluster.
 *
 * The cause was the same in all four: an inner per-request `.catch` that
 * substituted a falsy literal, so the rejection never reached `useWidgetData` --
 * which already logs the real error, shows a plain sentence and offers a scoped
 * refetch. The fix removes the swallowing, it does not add new machinery.
 *
 * An absence and a zero are not equally safe. A zero is worse: an absence invites
 * a second look, a number does not.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), getWithRetry: jest.fn() },
}));

import { api } from "@/lib/api";
import { QueueDepthWidget } from "./QueueDepthWidget";
import { RunningJobsWidget } from "./RunningJobsWidget";
import { TeamOutputWidget } from "./TeamOutputWidget";
import { MySessionsWidget } from "./MySessionsWidget";

const mockGet = api.get as jest.Mock;
const mockRetryGet = api.getWithRetry as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockRetryGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

/** Reject whichever getter the widget uses, however many calls it makes. */
function failEverything() {
  mockGet.mockRejectedValue(new Error("injected 500"));
  mockRetryGet.mockRejectedValue(new Error("injected 500"));
}

const CASES: { name: string; Widget: () => React.JSX.Element; testId: string; forbidden: RegExp }[] = [
  {
    name: "Queue Depth",
    Widget: QueueDepthWidget,
    testId: "widget-queue-depth",
    // The whole defect: a zero where the truth is "unknown".
    forbidden: /pending jobs/i,
  },
  {
    name: "Running Jobs",
    Widget: RunningJobsWidget,
    testId: "widget-running-jobs",
    forbidden: /pending|View all runs/i,
  },
  {
    name: "Team output",
    Widget: TeamOutputWidget,
    testId: "widget-team-output",
    forbidden: /runs completed|experiments started/i,
  },
  {
    name: "My active sessions",
    Widget: MySessionsWidget,
    testId: "widget-my-sessions",
    forbidden: /No active sessions/i,
  },
];

describe.each(CASES)("$name, when its data cannot be loaded", ({ Widget, testId, forbidden }) => {
  it("says so instead of rendering a value", async () => {
    failEverything();
    render(<Widget />);

    await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());

    const widget = screen.getByTestId(testId);
    expect(widget.textContent ?? "").not.toMatch(forbidden);
  });

  it("shows the house sentence, not a technical detail", async () => {
    failEverything();
    render(<Widget />);

    await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());

    const text = screen.getByTestId("widget-error").textContent ?? "";
    expect(text).toMatch(/could not be loaded, so nothing is shown here/i);
    expect(text).not.toMatch(/injected 500|Error:|TypeError|undefined/);
  });

  it("puts the real error in the logs", async () => {
    failEverything();
    render(<Widget />);

    await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());

    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining("[bioAF]"),
      expect.any(Error),
    );
  });

  it("offers a Retry that recovers just this widget", async () => {
    failEverything();
    render(<Widget />);
    await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());

    const retry = screen.getByRole("button", { name: /retry/i });
    expect(retry).toBeEnabled();

    const callsBefore = mockGet.mock.calls.length + mockRetryGet.mock.calls.length;
    await userEvent.click(retry);
    await waitFor(() =>
      expect(mockGet.mock.calls.length + mockRetryGet.mock.calls.length).toBeGreaterThan(callsBefore),
    );
  });
});

/**
 * A partial failure is still a failure. These two widgets each read two
 * endpoints; the old code let one succeed and the other silently contribute a
 * zero, which is a wrong total presented as a right one.
 */
describe("a widget that reads two endpoints", () => {
  it("Running Jobs does not report a total when only one count loaded", async () => {
    mockRetryGet
      .mockResolvedValueOnce({ total: 7 })
      .mockRejectedValueOnce(new Error("pending count down"));
    render(<RunningJobsWidget />);

    await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
    expect(screen.getByTestId("widget-running-jobs").textContent ?? "").not.toMatch(/\b7\b/);
  });

  it("Team output does not report a total when only one half loaded", async () => {
    mockRetryGet
      .mockResolvedValueOnce({ runs: [] })
      .mockRejectedValueOnce(new Error("experiments down"));
    render(<TeamOutputWidget />);

    await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
    expect(screen.getByTestId("widget-team-output").textContent ?? "").not.toMatch(
      /runs completed/i,
    );
  });
});

/** The healthy paths must keep working, including a genuine zero. */
describe("the honest zero still renders", () => {
  it("Queue Depth shows 0 when the queue really is empty", async () => {
    mockGet.mockResolvedValue({ runs: [], total: 0 });
    mockRetryGet.mockResolvedValue({ runs: [], total: 0 });
    render(<QueueDepthWidget />);

    await waitFor(() => expect(screen.getByText("pending jobs")).toBeInTheDocument());
    expect(screen.queryByTestId("widget-error")).not.toBeInTheDocument();
    expect(screen.getByTestId("widget-queue-depth").textContent).toMatch(/0/);
  });

  it("My active sessions says so when there genuinely are none", async () => {
    mockRetryGet.mockResolvedValue({ sessions: [] });
    render(<MySessionsWidget />);

    await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
    expect(screen.queryByTestId("widget-error")).not.toBeInTheDocument();
  });
});
