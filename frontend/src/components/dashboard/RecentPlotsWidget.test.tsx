import { render, screen, waitFor } from "@testing-library/react";
import { RecentPlotsWidget } from "./RecentPlotsWidget";

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

test("renders plots with a link to the archive", async () => {
  mockGet.mockResolvedValueOnce({
    plots: [{ id: 1, title: "UMAP", source_type: "notebook", indexed_at: new Date().toISOString() }],
    total: 1,
  });
  render(<RecentPlotsWidget />);
  await waitFor(() => expect(screen.getByText("UMAP")).toBeInTheDocument());
  expect(screen.getByText("View plot archive")).toHaveAttribute("href", "/results/plot-archive");
});

test("empty state", async () => {
  mockGet.mockResolvedValueOnce({ plots: [], total: 0 });
  render(<RecentPlotsWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("error state", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<RecentPlotsWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
