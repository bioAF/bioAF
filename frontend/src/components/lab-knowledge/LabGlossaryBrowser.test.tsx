import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { LabGlossaryBrowser } from "./LabGlossaryBrowser";

let canAccessImpl = (_resource: string, _action: string) => true;

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (r: string, a: string) => canAccessImpl(r, a) }),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn(), upload: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

const makeTerm = (overrides = {}) => ({
  id: 1,
  term: "Visually Acceptable Oocyte",
  definition: "An oocyte that meets the morphology bar at intake.",
  aliases: ["VAO"],
  category: "QC",
  context: null,
  source: "manual",
  created_by: { id: 1, name: "Alice", email: "alice@example.com" },
  created_at: "2026-01-15T10:00:00Z",
  updated_at: "2026-01-15T10:00:00Z",
  ...overrides,
});

beforeEach(() => {
  canAccessImpl = () => true;
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/glossary/pending")) return Promise.resolve({ pending_review_count: 0 });
    return Promise.resolve({ terms: [], total: 0, page: 1, page_size: 50 });
  });
});

test("shows loading state initially", () => {
  mockGet.mockImplementation(() => new Promise(() => {}));
  render(<LabGlossaryBrowser />);
  expect(screen.getByTestId("glossary-loading")).toBeInTheDocument();
});

test("renders empty state when no terms", async () => {
  render(<LabGlossaryBrowser />);
  await waitFor(() => {
    expect(screen.getByText(/No terms yet/i)).toBeInTheDocument();
  });
});

test("renders term rows", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/glossary/pending")) return Promise.resolve({ pending_review_count: 0 });
    return Promise.resolve({ terms: [makeTerm()], total: 1, page: 1, page_size: 50 });
  });
  render(<LabGlossaryBrowser />);
  await waitFor(() => {
    expect(screen.getByText("Visually Acceptable Oocyte")).toBeInTheDocument();
  });
});

test("add/import/scan hidden without manage permission", async () => {
  canAccessImpl = (_r, a) => a === "view";
  render(<LabGlossaryBrowser />);
  await waitFor(() => screen.getByText(/No terms yet/i));
  expect(screen.queryByRole("button", { name: /add term/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /import csv/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^scan$/i })).not.toBeInTheDocument();
});

test("management actions shown with manage permission", async () => {
  render(<LabGlossaryBrowser />);
  await waitFor(() => screen.getByText(/No terms yet/i));
  expect(screen.getByRole("button", { name: /add term/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /import csv/i })).toBeInTheDocument();
});

test("source filter requests terms scoped to the source", async () => {
  render(<LabGlossaryBrowser />);
  await waitFor(() => screen.getByText(/No terms yet/i));
  fireEvent.change(screen.getByLabelText(/filter by source/i), { target: { value: "llm_scan" } });
  await waitFor(() => {
    expect(mockGet.mock.calls.some(([url]) => url.includes("source=llm_scan"))).toBe(true);
  });
});

test("search requests terms with the query", async () => {
  render(<LabGlossaryBrowser />);
  await waitFor(() => screen.getByText(/No terms yet/i));
  fireEvent.change(screen.getByLabelText(/search glossary/i), { target: { value: "oocyte" } });
  await waitFor(() => {
    expect(mockGet.mock.calls.some(([url]) => url.includes("q=oocyte"))).toBe(true);
  });
});

test("pending review banner shown when proposals await review", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/glossary/pending")) return Promise.resolve({ pending_review_count: 3 });
    return Promise.resolve({ terms: [], total: 0, page: 1, page_size: 50 });
  });
  render(<LabGlossaryBrowser />);
  await waitFor(() => {
    expect(screen.getByText(/3 proposed terms awaiting review/i)).toBeInTheDocument();
  });
});
