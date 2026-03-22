import { render, screen, waitFor } from "@testing-library/react";
import { IngestStatusWidget } from "./IngestStatusWidget";

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
  api: {
    get: jest.fn(),
    getWithRetry: jest.fn(),
  },
}));

jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
}));

import { api } from "@/lib/api";

const mockGet = api.getWithRetry as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
});

test("renders file breakdown by source and type", async () => {
  mockGet.mockResolvedValueOnce({
    artifacts: { total: 42, by_type: { pdf: 12, png: 18, h5ad: 12 } },
    uploaded: { total: 8, by_type: { fastq: 8 } },
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    expect(screen.getByText("Artifacts")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("Uploaded")).toBeInTheDocument();
  });
  expect(screen.getByText("pdf")).toBeInTheDocument();
  expect(screen.getByText("png")).toBeInTheDocument();
  expect(screen.getByText("fastq")).toBeInTheDocument();
});

test("shows empty state when no files exist", async () => {
  mockGet.mockResolvedValueOnce({
    artifacts: { total: 0, by_type: {} },
    uploaded: { total: 0, by_type: {} },
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    expect(screen.getByTestId("widget-empty")).toBeInTheDocument();
  });
  expect(screen.getByText("No files yet.")).toBeInTheDocument();
});

test("shows loading state initially", () => {
  mockGet.mockImplementation(() => new Promise(() => {}));
  render(<IngestStatusWidget />);
  expect(screen.getByTestId("widget-loading")).toBeInTheDocument();
});

test("shows error when fetch fails", async () => {
  mockGet.mockRejectedValueOnce(new Error("network error"));

  render(<IngestStatusWidget />);

  await waitFor(() => {
    expect(screen.getByTestId("widget-error")).toBeInTheDocument();
  });
});

test("hides section when its total is zero", async () => {
  mockGet.mockResolvedValueOnce({
    artifacts: { total: 5, by_type: { png: 5 } },
    uploaded: { total: 0, by_type: {} },
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    expect(screen.getByText("Artifacts")).toBeInTheDocument();
  });
  expect(screen.queryByText("Uploaded")).not.toBeInTheDocument();
});

test("links to files page", async () => {
  mockGet.mockResolvedValueOnce({
    artifacts: { total: 1, by_type: { pdf: 1 } },
    uploaded: { total: 0, by_type: {} },
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    const link = screen.getByText("View all files");
    expect(link).toHaveAttribute("href", "/data/files");
  });
});
