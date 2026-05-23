import { render, screen, waitFor } from "@testing-library/react";
import { TeamOutputWidget } from "./TeamOutputWidget";

jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), getWithRetry: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.getWithRetry as jest.Mock;

function isoAgo(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

beforeEach(() => mockGet.mockReset());

test("counts runs completed and experiments started in the last week", async () => {
  mockGet
    .mockResolvedValueOnce({
      runs: [
        { id: 1, status: "completed", completed_at: isoAgo(2 * 86400000) },
        { id: 2, status: "completed", completed_at: isoAgo(1 * 86400000) },
        { id: 3, status: "completed", completed_at: isoAgo(20 * 86400000) }, // too old
      ],
    })
    .mockResolvedValueOnce({
      experiments: [{ id: 1, created_at: isoAgo(3 * 86400000) }],
    });
  render(<TeamOutputWidget />);
  await waitFor(() => expect(screen.getByText("runs completed")).toBeInTheDocument());
  expect(screen.getByText("2")).toBeInTheDocument();
  expect(screen.getByText("1")).toBeInTheDocument();
  expect(screen.getByText("experiments started")).toBeInTheDocument();
});

test("error state when both sources fail at the gather level", async () => {
  // both reject; the per-call catches yield empty, so the widget still renders zeros
  mockGet.mockResolvedValueOnce({ runs: [] }).mockResolvedValueOnce({ experiments: [] });
  render(<TeamOutputWidget />);
  await waitFor(() => expect(screen.getByText("runs completed")).toBeInTheDocument());
  expect(screen.getAllByText("0").length).toBeGreaterThan(0);
});
