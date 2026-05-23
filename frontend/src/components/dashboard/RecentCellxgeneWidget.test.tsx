import { render, screen, waitFor } from "@testing-library/react";
import { RecentCellxgeneWidget } from "./RecentCellxgeneWidget";

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

test("renders cellxgene publications (bare array) with a view link", async () => {
  mockGet.mockResolvedValueOnce([
    { id: 3, dataset_name: "Liver atlas", status: "published", created_at: new Date().toISOString(), published_at: new Date().toISOString() },
  ]);
  render(<RecentCellxgeneWidget />);
  await waitFor(() => expect(screen.getByText("Liver atlas")).toBeInTheDocument());
  expect(screen.getByText("View cellxgene")).toHaveAttribute("href", "/results/cellxgene");
});

test("empty state", async () => {
  mockGet.mockResolvedValueOnce([]);
  render(<RecentCellxgeneWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("error state", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<RecentCellxgeneWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
