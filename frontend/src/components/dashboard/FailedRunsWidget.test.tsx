import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { FailedRunsWidget } from "./FailedRunsWidget";

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

function isoAgo(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

function failed(id: number, name: string, ageMs: number) {
  return {
    id,
    pipeline_name: name,
    status: "failed",
    created_at: isoAgo(ageMs),
    completed_at: isoAgo(ageMs),
  };
}

beforeEach(() => mockGet.mockReset());

test("shows failed runs within the default 24h window", async () => {
  mockGet.mockResolvedValueOnce({
    runs: [failed(1, "rnaseq", 2 * 3600 * 1000), failed(2, "atac", 30 * 60 * 1000)],
    total: 2,
  });
  render(<FailedRunsWidget />);
  await waitFor(() => expect(screen.getByText("rnaseq")).toBeInTheDocument());
  expect(screen.getByText("atac")).toBeInTheDocument();
});

test("the 1h window filters out older failures", async () => {
  mockGet.mockResolvedValueOnce({
    runs: [failed(1, "rnaseq", 2 * 3600 * 1000), failed(2, "atac", 30 * 60 * 1000)],
    total: 2,
  });
  render(<FailedRunsWidget />);
  await waitFor(() => expect(screen.getByText("rnaseq")).toBeInTheDocument());
  fireEvent.click(screen.getByTestId("failed-window-1h"));
  expect(screen.queryByText("rnaseq")).not.toBeInTheDocument();
  expect(screen.getByText("atac")).toBeInTheDocument();
});

test("empty when nothing failed in the window", async () => {
  mockGet.mockResolvedValueOnce({ runs: [failed(1, "old", 48 * 3600 * 1000)], total: 1 });
  render(<FailedRunsWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("error state on fetch failure", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<FailedRunsWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
