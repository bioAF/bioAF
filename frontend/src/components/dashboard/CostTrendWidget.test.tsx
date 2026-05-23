import { render, screen, waitFor } from "@testing-library/react";
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

test("error state", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<CostTrendWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
