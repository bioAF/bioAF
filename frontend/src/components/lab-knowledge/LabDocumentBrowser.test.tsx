import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { LabDocumentBrowser } from "./LabDocumentBrowser";

let canAccessImpl = (_resource: string, _action: string) => true;

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (r: string, a: string) => canAccessImpl(r, a) }),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

const makeDoc = (overrides = {}) => ({
  id: 1,
  title: "Centrifuge Manual",
  description: "How to spin",
  file_name: "manual.pdf",
  current_version: 1,
  file_size_bytes: 2048,
  mime_type: "application/pdf",
  md5_checksum: "abc123",
  is_archived: false,
  tags: [{ id: 7, name: "manual" }],
  created_by: { id: 1, name: "Alice", email: "alice@example.com" },
  created_at: "2026-01-15T10:00:00Z",
  updated_at: "2026-01-15T10:00:00Z",
  ...overrides,
});

beforeEach(() => {
  canAccessImpl = () => true;
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/document-tags")) return Promise.resolve([{ id: 7, name: "manual" }]);
    return Promise.resolve({ documents: [], total: 0, page: 1, page_size: 25 });
  });
});

test("shows loading state initially", () => {
  mockGet.mockImplementation(() => new Promise(() => {}));
  render(<LabDocumentBrowser />);
  expect(screen.getByTestId("lab-docs-loading")).toBeInTheDocument();
});

test("renders empty state when no documents", async () => {
  render(<LabDocumentBrowser />);
  await waitFor(() => {
    expect(screen.getByText("No documents found.")).toBeInTheDocument();
  });
});

test("renders document rows", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/document-tags")) return Promise.resolve([{ id: 7, name: "manual" }]);
    return Promise.resolve({ documents: [makeDoc()], total: 1, page: 1, page_size: 25 });
  });
  render(<LabDocumentBrowser />);
  await waitFor(() => {
    expect(screen.getByText("Centrifuge Manual")).toBeInTheDocument();
  });
});

test("upload button hidden without manage permission", async () => {
  canAccessImpl = (_r, a) => a === "view";
  render(<LabDocumentBrowser />);
  await waitFor(() => screen.getByText("No documents found."));
  expect(screen.queryByRole("button", { name: /upload document/i })).not.toBeInTheDocument();
});

test("upload button shown with manage permission", async () => {
  render(<LabDocumentBrowser />);
  await waitFor(() => screen.getByText("No documents found."));
  expect(screen.getByRole("button", { name: /upload document/i })).toBeInTheDocument();
});

test("tag filter requests documents scoped to the tag", async () => {
  render(<LabDocumentBrowser />);
  await waitFor(() => screen.getByText("No documents found."));
  // The "manual" tag chip comes from the tags fetch.
  fireEvent.click(screen.getByRole("button", { name: "manual" }));
  await waitFor(() => {
    expect(mockGet.mock.calls.some(([url]) => url.includes("tag_ids=7"))).toBe(true);
  });
});

test("show-archived toggle requests archived documents", async () => {
  render(<LabDocumentBrowser />);
  await waitFor(() => screen.getByText("No documents found."));
  fireEvent.click(screen.getByLabelText(/show archived/i));
  await waitFor(() => {
    expect(mockGet.mock.calls.some(([url]) => url.includes("include_archived=true"))).toBe(true);
  });
});
