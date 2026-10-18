import { render, screen, waitFor } from "@testing-library/react";
import { CostBudgetWidget } from "./CostBudgetWidget";

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
const mockStatus = api.get as jest.Mock;
const mockGet = api.getWithRetry as jest.Mock;

const SUMMARY = {
  current_month_spend: 40,
  monthly_budget: 100,
  budget_remaining: 60,
  projected_month_end: 80,
  breakdown_by_component: [],
  currency: "USD",
};

beforeEach(() => {
  mockStatus.mockReset();
  mockGet.mockReset();
});

test("before the billing dashboard is set up, shows no spend and never asks for it", async () => {
  mockStatus.mockResolvedValueOnce({ configured: false, dataset_id: "" });
  render(<CostBudgetWidget />);
  await waitFor(() =>
    expect(screen.getByText("Please enable the billing dashboard")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("widget-cost-budget")).not.toHaveTextContent("$");
  expect(mockGet).not.toHaveBeenCalled();
});

test("while billing data is pending, shows Pending and never asks for spend", async () => {
  mockStatus.mockResolvedValueOnce({ configured: false, dataset_id: "billing_export" });
  render(<CostBudgetWidget />);
  await waitFor(() => expect(screen.getByText("Pending")).toBeInTheDocument());
  expect(mockGet).not.toHaveBeenCalled();
});

test("once set up, shows spend against the budget", async () => {
  mockStatus.mockResolvedValueOnce({ configured: true, dataset_id: "billing_export" });
  mockGet.mockResolvedValueOnce(SUMMARY);
  render(<CostBudgetWidget />);
  await waitFor(() => expect(screen.getByText("$40.00")).toBeInTheDocument());
  expect(screen.getByText("$100.00")).toBeInTheDocument();
  expect(screen.getByText("40% of monthly budget")).toBeInTheDocument();
});
