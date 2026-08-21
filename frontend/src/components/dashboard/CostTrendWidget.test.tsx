import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { CostTrendWidget } from "./CostTrendWidget";

jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});
jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), getWithRetry: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.getWithRetry as jest.Mock;

beforeEach(() => mockGet.mockReset());

test("renders the total and a bar per day", async () => {
  mockGet.mockResolvedValueOnce({
    records: [
      { date: "2026-05-01", amount: 10 },
      { date: "2026-05-02", amount: 20 },
    ],
    total_amount: 30,
  });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByText("$30.00")).toBeInTheDocument());
  expect(screen.getByTestId("cost-trend-chart").children.length).toBe(2);
  expect(screen.getByText("View cost center")).toHaveAttribute("href", "/infrastructure/cost-center");
});

test("requests a 30-day window", async () => {
  mockGet.mockResolvedValueOnce({ records: [], total_amount: 0 });
  render(<CostTrendWidget />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(mockGet.mock.calls[0][0]).toMatch(/^\/api\/costs\/history\?start_date=\d{4}-\d{2}-\d{2}&end_date=\d{4}-\d{2}-\d{2}$/);
});

test("empty state with no history", async () => {
  mockGet.mockResolvedValueOnce({ records: [], total_amount: 0 });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("the empty state claims nothing beyond the window it asked about", async () => {
  // The widget asks for 30 days, so it cannot know whether cost has ever been
  // recorded. "No cost history yet" asserts none ever existed.
  mockGet.mockResolvedValueOnce({ records: [], total_amount: 0 });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
  expect(screen.getByTestId("widget-empty")).toHaveTextContent(/last 30 days/i);
  expect(screen.getByTestId("widget-empty")).not.toHaveTextContent(/yet/i);
});

test("the empty state still offers the way to the full record", async () => {
  mockGet.mockResolvedValueOnce({ records: [], total_amount: 0 });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
  expect(screen.getByText("View cost center")).toHaveAttribute(
    "href",
    "/infrastructure/cost-center",
  );
});

test("error state", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});

// The owner, on the running app 2026-08-09: "I see a graphic, but cannot pull
// real numbers from it." The chart carried a native `title` attribute and
// nothing else: no axis, no labels, no readout.

const days = (n: number) =>
  Array.from({ length: n }, (_, i) => ({
    date: `2026-08-${String(i + 1).padStart(2, "0")}`,
    amount: 10 + i,
  }));

test("each bar carries the day it represents, under the chart", async () => {
  mockGet.mockResolvedValueOnce({
    records: [
      { date: "2026-07-31", amount: 5 },
      { date: "2026-08-01", amount: 10 },
      { date: "2026-08-02", amount: 20 },
    ],
    total_amount: 35,
  });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("cost-trend-chart")).toBeInTheDocument());

  const labels = screen.getAllByTestId("cost-trend-label");
  expect(labels.map((l) => l.textContent)).toEqual(["31", "1", "2"]);
});

test("hovering a bar reports that day's date and total", async () => {
  mockGet.mockResolvedValueOnce({
    records: [
      { date: "2026-08-01", amount: 10 },
      { date: "2026-08-02", amount: 22.5 },
    ],
    total_amount: 32.5,
  });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("cost-trend-chart")).toBeInTheDocument());

  expect(screen.queryByTestId("cost-trend-tooltip")).not.toBeInTheDocument();

  fireEvent.mouseEnter(screen.getAllByTestId("cost-trend-bar")[1]);
  const tip = screen.getByTestId("cost-trend-tooltip");
  expect(tip).toHaveTextContent("Aug 2");
  expect(tip).toHaveTextContent("$22.50");

  fireEvent.mouseLeave(screen.getAllByTestId("cost-trend-bar")[1]);
  expect(screen.queryByTestId("cost-trend-tooltip")).not.toBeInTheDocument();
});

test("a date is read off the string, not through a timezone", async () => {
  // `new Date("2026-08-01")` is UTC midnight, which renders as Jul 31 in any
  // negative-offset timezone. Every date here is a plain calendar day.
  mockGet.mockResolvedValueOnce({
    records: [{ date: "2026-08-01", amount: 10 }],
    total_amount: 10,
  });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("cost-trend-chart")).toBeInTheDocument());

  fireEvent.mouseEnter(screen.getAllByTestId("cost-trend-bar")[0]);
  expect(screen.getByTestId("cost-trend-tooltip")).toHaveTextContent("Aug 1");
  expect(screen.getAllByTestId("cost-trend-label")[0]).toHaveTextContent("1");
});

test("labels thin out when there are too many bars to label each one", async () => {
  // Measured on the deployed widget: the chart is 189px wide at a 1024px
  // viewport, so a full 30-day window gives each bar about 4.4px. A label under
  // every bar is not possible at that width, and pretending otherwise would
  // overlap them into mush.
  mockGet.mockResolvedValueOnce({ records: days(30), total_amount: 700 });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("cost-trend-chart")).toBeInTheDocument());

  const bars = screen.getAllByTestId("cost-trend-bar");
  const labels = screen.getAllByTestId("cost-trend-label");
  expect(bars).toHaveLength(30);
  expect(labels.length).toBeLessThan(30);
  expect(labels.length).toBeGreaterThan(5);
});

test("the most recent day is always labelled", async () => {
  mockGet.mockResolvedValueOnce({ records: days(30), total_amount: 700 });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("cost-trend-chart")).toBeInTheDocument());

  const labels = screen.getAllByTestId("cost-trend-label");
  expect(labels[labels.length - 1]).toHaveTextContent("30");
});

test("every value is still readable without a mouse", async () => {
  // A hover tooltip is not available to a screen reader or a keyboard, so the
  // same numbers are in the document as text.
  mockGet.mockResolvedValueOnce({
    records: [
      { date: "2026-08-01", amount: 10 },
      { date: "2026-08-02", amount: 22.5 },
    ],
    total_amount: 32.5,
  });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("cost-trend-chart")).toBeInTheDocument());

  const table = screen.getByTestId("cost-trend-table");
  expect(within(table).getByText("Aug 2")).toBeInTheDocument();
  expect(within(table).getByText("$22.50")).toBeInTheDocument();
  expect(within(table).getByText("Aug 1")).toBeInTheDocument();
  expect(within(table).getByText("$10.00")).toBeInTheDocument();
});

test("the chart is one tab stop, and arrows walk the days", async () => {
  // Not one tab stop per bar: a 30-day window would put 30 stops in the middle
  // of the dashboard. One focusable chart, arrows to move, Escape to clear.
  mockGet.mockResolvedValueOnce({
    records: [
      { date: "2026-08-01", amount: 10 },
      { date: "2026-08-02", amount: 22.5 },
      { date: "2026-08-03", amount: 30 },
    ],
    total_amount: 62.5,
  });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("cost-trend-chart")).toBeInTheDocument());

  const chart = screen.getByTestId("cost-trend-chart");
  expect(chart).toHaveAttribute("tabindex", "0");
  expect(screen.getAllByTestId("cost-trend-bar")[0]).not.toHaveAttribute("tabindex");

  fireEvent.keyDown(chart, { key: "ArrowRight" });
  expect(screen.getByTestId("cost-trend-tooltip")).toHaveTextContent("Aug 1");

  fireEvent.keyDown(chart, { key: "ArrowRight" });
  expect(screen.getByTestId("cost-trend-tooltip")).toHaveTextContent("Aug 2");

  fireEvent.keyDown(chart, { key: "ArrowLeft" });
  expect(screen.getByTestId("cost-trend-tooltip")).toHaveTextContent("Aug 1");

  fireEvent.keyDown(chart, { key: "End" });
  expect(screen.getByTestId("cost-trend-tooltip")).toHaveTextContent("Aug 3");

  fireEvent.keyDown(chart, { key: "Escape" });
  expect(screen.queryByTestId("cost-trend-tooltip")).not.toBeInTheDocument();
});

test("the arrow keys stop at the ends rather than wrapping", async () => {
  mockGet.mockResolvedValueOnce({
    records: [
      { date: "2026-08-01", amount: 10 },
      { date: "2026-08-02", amount: 20 },
    ],
    total_amount: 30,
  });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("cost-trend-chart")).toBeInTheDocument());

  const chart = screen.getByTestId("cost-trend-chart");
  fireEvent.keyDown(chart, { key: "Home" });
  fireEvent.keyDown(chart, { key: "ArrowLeft" });
  expect(screen.getByTestId("cost-trend-tooltip")).toHaveTextContent("Aug 1");

  fireEvent.keyDown(chart, { key: "End" });
  fireEvent.keyDown(chart, { key: "ArrowRight" });
  expect(screen.getByTestId("cost-trend-tooltip")).toHaveTextContent("Aug 2");
});

test("the screen-reader table is hidden without taking up layout space", async () => {
  // `sr-only` hides an element by pinning it to a 1px box. That does not work on
  // a <table>: CSS treats height on a table box as a MINIMUM, so the table stays
  // as tall as its rows. `clip` still hides it from view, so the result is an
  // invisible 440px block, and because `sr-only` also makes it absolute with no
  // positioned ancestor it escapes the dashboard's scroll container and stretches
  // the whole document instead.
  //
  // Measured on the deployed demo 2026-08-15 at a 1440x900 viewport: 16 rows,
  // documentElement.scrollHeight 1059 against a 900 viewport, so the entire app
  // scrolled. The bug is data-dependent, which is why it only appeared once the
  // month had accumulated enough days to push the table past the fold.
  //
  // The hiding box therefore has to be an element that honours a fixed height.
  mockGet.mockResolvedValueOnce({
    records: Array.from({ length: 30 }, (_, i) => ({
      date: `2026-08-${String(i + 1).padStart(2, "0")}`,
      amount: 10 + i,
    })),
    total_amount: 675,
  });
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("cost-trend-chart")).toBeInTheDocument());

  const table = screen.getByTestId("cost-trend-table");
  expect(table.className).not.toMatch(/\bsr-only\b/);

  const hider = table.closest(".sr-only");
  expect(hider).not.toBeNull();
  expect(hider!.tagName).not.toBe("TABLE");
});
