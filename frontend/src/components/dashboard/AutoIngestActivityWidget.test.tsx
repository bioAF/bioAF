import { render, screen, waitFor } from "@testing-library/react";
import { AutoIngestActivityWidget } from "./AutoIngestActivityWidget";

jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), getWithRetry: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.getWithRetry as jest.Mock;

beforeEach(() => mockGet.mockReset());

test("renders processed, failed, and enabled state", async () => {
  mockGet.mockResolvedValueOnce({
    enabled: true,
    messages_processed_24h: 42,
    messages_failed_24h: 3,
  });
  render(<AutoIngestActivityWidget />);
  await waitFor(() => expect(screen.getByText("42")).toBeInTheDocument());
  expect(screen.getByText("3")).toBeInTheDocument();
  expect(screen.getByText("Enabled")).toBeInTheDocument();
  expect(screen.getByText("processed (24h)")).toBeInTheDocument();
});

test("shows Disabled when auto-ingest is off", async () => {
  mockGet.mockResolvedValueOnce({
    enabled: false,
    messages_processed_24h: 0,
    messages_failed_24h: 0,
  });
  render(<AutoIngestActivityWidget />);
  await waitFor(() => expect(screen.getByText("Disabled")).toBeInTheDocument());
});

test("error state", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<AutoIngestActivityWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
