import { render, screen, waitFor } from "@testing-library/react";
import { IngestStatusWidget } from "./IngestStatusWidget";

// Mock next/link
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

// Mock the api module
jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    getWithRetry: jest.fn(),
  },
}));

// Mock auth so the token is always set
jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
}));

import { api } from "@/lib/api";

const mockGet = api.getWithRetry as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
});

test("renders file count from /api/files endpoint", async () => {
  mockGet.mockImplementation((path: string) => {
    if (path.startsWith("/api/files")) {
      return Promise.resolve({ files: [], total: 14, page: 1, page_size: 1 });
    }
    // ingest endpoints return arrays
    return Promise.resolve([]);
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    expect(screen.getByText("14")).toBeInTheDocument();
  });

  expect(screen.getByText("Files ingested")).toBeInTheDocument();
});

test("renders zero file count explicitly", async () => {
  mockGet.mockImplementation((path: string) => {
    if (path.startsWith("/api/files")) {
      return Promise.resolve({ files: [], total: 0, page: 1, page_size: 1 });
    }
    return Promise.resolve([]);
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});

test("renders unmatched count from array response", async () => {
  const unmatchedEvents = [
    { id: 1, ingest_status: "unmatched", source_path: "a.fastq" },
    { id: 2, ingest_status: "unmatched", source_path: "b.fastq" },
  ];

  mockGet.mockImplementation((path: string) => {
    if (path.startsWith("/api/files")) {
      return Promise.resolve({ files: [], total: 5, page: 1, page_size: 1 });
    }
    if (path === "/api/ingest/unmatched") {
      return Promise.resolve(unmatchedEvents);
    }
    return Promise.resolve([]);
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    expect(screen.getByText("Unmatched")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });
});

test("renders unclaimed count from array response", async () => {
  const unclaimedEntities = [
    { entity_type: "project", entity_id: 1, name: "P1", created_at: "" },
  ];

  mockGet.mockImplementation((path: string) => {
    if (path.startsWith("/api/files")) {
      return Promise.resolve({ files: [], total: 3, page: 1, page_size: 1 });
    }
    if (path === "/api/ingest/unclaimed") {
      return Promise.resolve(unclaimedEntities);
    }
    return Promise.resolve([]);
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    expect(screen.getByText("Unclaimed")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });
});

test("shows loading state initially", () => {
  mockGet.mockImplementation(() => new Promise(() => {})); // never resolves
  render(<IngestStatusWidget />);
  expect(screen.getByTestId("widget-loading")).toBeInTheDocument();
});

test("degrades gracefully when all fetches fail", async () => {
  mockGet.mockRejectedValue(new Error("network error"));

  render(<IngestStatusWidget />);

  // Individual catch handlers return fallback values, so widget shows zeros
  await waitFor(() => {
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  expect(screen.queryByTestId("widget-error")).not.toBeInTheDocument();
});

test("hides unmatched and unclaimed when both are zero", async () => {
  mockGet.mockImplementation((path: string) => {
    if (path.startsWith("/api/files")) {
      return Promise.resolve({ files: [], total: 7, page: 1, page_size: 1 });
    }
    return Promise.resolve([]);
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  expect(screen.queryByText("Unmatched")).not.toBeInTheDocument();
  expect(screen.queryByText("Unclaimed")).not.toBeInTheDocument();
});

test("links to ingest activity page", async () => {
  mockGet.mockImplementation((path: string) => {
    if (path.startsWith("/api/files")) {
      return Promise.resolve({ files: [], total: 0, page: 1, page_size: 1 });
    }
    return Promise.resolve([]);
  });

  render(<IngestStatusWidget />);

  await waitFor(() => {
    const link = screen.getByText("View ingest activity");
    expect(link).toHaveAttribute("href", "/data/upload");
  });
});
