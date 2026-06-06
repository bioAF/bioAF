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
const mockPost = api.post as jest.Mock;

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
  mockPost.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/glossary/pending"))
      return Promise.resolve({ pending_review_count: 0, job_ids: [] });
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
  expect(screen.queryByRole("button", { name: /ai scan/i })).not.toBeInTheDocument();
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
    if (url.includes("/glossary/pending"))
      return Promise.resolve({ pending_review_count: 3, job_ids: [9] });
    return Promise.resolve({ terms: [], total: 0, page: 1, page_size: 50 });
  });
  render(<LabGlossaryBrowser />);
  await waitFor(() => {
    expect(screen.getByText(/3 proposed terms awaiting review/i)).toBeInTheDocument();
  });
});

test("the AI scan modal names AI and the org LLM provider", async () => {
  render(<LabGlossaryBrowser />);
  await waitFor(() => screen.getByText(/No terms yet/i));
  fireEvent.click(screen.getByRole("button", { name: /ai scan/i }));
  expect(screen.getByText(/Run AI Glossary Scan/i)).toBeInTheDocument();
  expect(screen.getByText(/LLM provider/i)).toBeInTheDocument();
});

test("shows a running banner while an AI scan is in progress", async () => {
  mockPost.mockResolvedValue({ id: 7, scan_type: "topic", status: "pending" });
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/glossary/scan/7"))
      return Promise.resolve({ id: 7, scan_type: "topic", status: "running" });
    if (url.includes("/glossary/pending"))
      return Promise.resolve({ pending_review_count: 0, job_ids: [] });
    return Promise.resolve({ terms: [], total: 0, page: 1, page_size: 50 });
  });
  render(<LabGlossaryBrowser />);
  await waitFor(() => screen.getByText(/No terms yet/i));
  fireEvent.click(screen.getByRole("button", { name: /ai scan/i }));
  fireEvent.change(screen.getByLabelText(/topic/i), {
    target: { value: "single-cell RNA-seq" },
  });
  fireEvent.click(screen.getByRole("button", { name: /start ai scan/i }));
  await waitFor(() => {
    expect(screen.getByTestId("scan-running-banner")).toBeInTheDocument();
  });
});

test("clicking the pending banner opens the review modal", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/glossary/pending"))
      return Promise.resolve({ pending_review_count: 2, job_ids: [5] });
    if (url.includes("/glossary/scan/5/proposals"))
      return Promise.resolve({
        job: { id: 5, scan_type: "topic", status: "complete" },
        new_terms: [
          {
            id: 11,
            term: "Spheroid",
            proposed_definition: "A 3D cluster of cells.",
            proposed_aliases: null,
            proposed_category: null,
            proposed_context: null,
            proposal_type: "new",
            existing_term_id: null,
            existing_definition: null,
            source_description: null,
            previously_rejected: false,
            review_status: "pending",
          },
        ],
        changed_terms: [],
      });
    return Promise.resolve({ terms: [], total: 0, page: 1, page_size: 50 });
  });
  render(<LabGlossaryBrowser />);
  await waitFor(() => screen.getByText(/2 proposed terms awaiting review/i));
  fireEvent.click(screen.getByText(/2 proposed terms awaiting review/i));
  await waitFor(() => {
    expect(screen.getByText(/Review Proposed Terms/i)).toBeInTheDocument();
  });
  expect(screen.getByText("Spheroid")).toBeInTheDocument();
});
