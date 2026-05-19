import { render, screen, waitFor } from "@testing-library/react";
import { LiteratureTabPanel } from "./LiteratureTabPanel";

jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ role_name: "admin" }),
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    delete: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
  },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

const makePaper = (overrides = {}) => ({
  id: 1,
  title: "Stiff matrix and migration",
  authors: [{ given: "Sarah", family: "Chen" }],
  publication_date: "2024-05-12",
  journal: "Nature Methods",
  doi: "10.test/1",
  pmid: null,
  abstract: null,
  provenance: "user_upload",
  source: "upload",
  added_by_user_id: 1,
  has_pdf: false,
  has_full_text: false,
  extraction_status: "none",
  extraction_error: null,
  comment_count: 0,
  reading_status: null,
  dismissed: false,
  in_library: true,
  associations: [],
  created_at: "2026-05-19T00:00:00Z",
  updated_at: "2026-05-19T00:00:00Z",
  ...overrides,
});

beforeEach(() => {
  mockGet.mockReset();
});

test("experimentId triggers a request with experiment_id and include_parent_project=true", async () => {
  mockGet.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
  render(<LiteratureTabPanel experimentId={42} />);
  await waitFor(() => {
    const calls = mockGet.mock.calls.map(([url]: [string]) => url);
    expect(
      calls.some(
        (url: string) =>
          url.includes("/api/literature/papers") &&
          url.includes("experiment_id=42") &&
          url.includes("include_parent_project=true"),
      ),
    ).toBe(true);
  });
});

test("projectId triggers a request with project_id and no include_parent_project flag", async () => {
  mockGet.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
  render(<LiteratureTabPanel projectId={7} />);
  await waitFor(() => {
    const calls = mockGet.mock.calls.map(([url]: [string]) => url);
    expect(
      calls.some(
        (url: string) =>
          url.includes("/api/literature/papers") && url.includes("project_id=7"),
      ),
    ).toBe(true);
    expect(
      calls.some((url: string) => url.includes("include_parent_project=true")),
    ).toBe(false);
  });
});

test("renders associated papers", async () => {
  mockGet.mockResolvedValue({
    items: [makePaper({ title: "Mechanotransduction" })],
    total: 1,
    page: 1,
    page_size: 50,
  });
  render(<LiteratureTabPanel projectId={7} />);
  await waitFor(() => {
    expect(screen.getByText("Mechanotransduction")).toBeInTheDocument();
  });
});

test("empty state when there are no associated papers", async () => {
  mockGet.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
  render(<LiteratureTabPanel projectId={7} />);
  await waitFor(() => {
    expect(
      screen.getByText(/no papers associated/i),
    ).toBeInTheDocument();
  });
});
