import { render, screen, waitFor } from "@testing-library/react";
import { BackupStatusWidget } from "./BackupStatusWidget";

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

test("renders overall status and each tier", async () => {
  mockGet.mockResolvedValueOnce({
    overall_status: "healthy",
    tiers: [
      { tier: "config", name: "Config snapshots", status: "healthy" },
      { tier: "postgres", name: "Database", status: "healthy" },
    ],
  });
  render(<BackupStatusWidget />);
  // "healthy" appears for the overall status and each tier
  await waitFor(() => expect(screen.getAllByText("healthy").length).toBeGreaterThanOrEqual(3));
  expect(screen.getByText("Config snapshots")).toBeInTheDocument();
  expect(screen.getByText("Database")).toBeInTheDocument();
  expect(screen.getByText("View backups")).toHaveAttribute("href", "/infrastructure/backup");
});

test("error state", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<BackupStatusWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
