import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ExperimentsStatusWidget } from "./ExperimentsStatusWidget";

jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return <a href={href}>{children}</a>;
  };
});

jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), getWithRetry: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.getWithRetry as jest.Mock;

beforeEach(() => mockGet.mockReset());

test("renders experiments with status badges and a link to all", async () => {
  mockGet.mockResolvedValueOnce({
    experiments: [
      { id: 1, name: "Liver scRNA", status: "active" },
      { id: 2, name: "Kidney bulk", status: "completed" },
    ],
    total: 2,
  });
  render(<ExperimentsStatusWidget />);
  await waitFor(() => {
    expect(screen.getByText("Liver scRNA")).toBeInTheDocument();
  });
  expect(screen.getByText("Active")).toBeInTheDocument();
  expect(screen.getByText("Completed")).toBeInTheDocument();
  expect(screen.getByText("View all experiments")).toHaveAttribute("href", "/experiments");
});

test("shows the empty state when there are no experiments", async () => {
  mockGet.mockResolvedValueOnce({ experiments: [], total: 0 });
  render(<ExperimentsStatusWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("shows the loading state initially", () => {
  mockGet.mockImplementation(() => new Promise(() => {}));
  render(<ExperimentsStatusWidget />);
  expect(screen.getByTestId("widget-loading")).toBeInTheDocument();
});

test("shows an error when the fetch fails", async () => {
  mockGet.mockRejectedValueOnce(new Error("boom"));
  render(<ExperimentsStatusWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});

// That no widget reloads the page at all is held repo-wide by
// src/__tests__/dashboard-widget-retry.test.ts.
test("Retry refetches this widget rather than starting over", async () => {
  mockGet.mockRejectedValueOnce(new Error("boom"));
  mockGet.mockResolvedValueOnce({ experiments: [{ id: 3, name: "Recovered", status: "active" }], total: 1 });

  render(<ExperimentsStatusWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(await screen.findByText("Recovered")).toBeInTheDocument();
  expect(screen.queryByTestId("widget-error")).not.toBeInTheDocument();
  expect(mockGet).toHaveBeenCalledTimes(2);
});
