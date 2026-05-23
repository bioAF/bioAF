import { render, screen, waitFor } from "@testing-library/react";
import { RunsAwaitingReviewWidget } from "./RunsAwaitingReviewWidget";

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

test("shows only completed runs without a review verdict", async () => {
  mockGet.mockResolvedValueOnce({
    runs: [
      { id: 1, pipeline_name: "needs-review", status: "completed", completed_at: new Date().toISOString(), review_verdict: null },
      { id: 2, pipeline_name: "already-approved", status: "completed", completed_at: new Date().toISOString(), review_verdict: "approved" },
    ],
    total: 2,
  });
  render(<RunsAwaitingReviewWidget />);
  await waitFor(() => expect(screen.getByText("needs-review")).toBeInTheDocument());
  expect(screen.queryByText("already-approved")).not.toBeInTheDocument();
});

test("empty when everything is reviewed", async () => {
  mockGet.mockResolvedValueOnce({
    runs: [
      { id: 1, pipeline_name: "done", status: "completed", completed_at: new Date().toISOString(), review_verdict: "approved" },
    ],
    total: 1,
  });
  render(<RunsAwaitingReviewWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("error state on fetch failure", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<RunsAwaitingReviewWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
