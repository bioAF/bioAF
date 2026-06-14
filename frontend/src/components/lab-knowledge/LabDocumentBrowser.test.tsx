import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { LabDocumentBrowser } from "./LabDocumentBrowser";

let canAccessImpl = (_resource: string, _action: string) => true;

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (r: string, a: string) => canAccessImpl(r, a) }),
}));

const pushMock = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

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
  pushMock.mockReset();
  mockGet.mockReset();
  mockPost.mockReset();
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

test("clicking a document row navigates to its detail page", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/document-tags")) return Promise.resolve([{ id: 7, name: "manual" }]);
    return Promise.resolve({ documents: [makeDoc()], total: 1, page: 1, page_size: 25 });
  });
  render(<LabDocumentBrowser />);
  await waitFor(() => screen.getByText("Centrifuge Manual"));
  fireEvent.click(screen.getByText("Centrifuge Manual"));
  expect(pushMock).toHaveBeenCalledWith("/lab-knowledge/documents/1");
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

test("upload modal offers both device and URL sources", async () => {
  render(<LabDocumentBrowser />);
  await waitFor(() => screen.getByText("No documents found."));
  fireEvent.click(screen.getByRole("button", { name: /upload document/i }));
  expect(screen.getByRole("button", { name: /from device/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /from url/i })).toBeInTheDocument();
});

test("device upload requests a sized resumable URL and PUTs to the session", async () => {
  // Regression for the "Failed to fetch" bug: the upload-url request now carries
  // size_bytes, and the bytes go to the returned resumable session URL via PUT.
  const realFetch = global.fetch;
  global.fetch = jest
    .fn()
    .mockResolvedValue({ status: 200, ok: true, headers: { get: () => null } });
  mockPost.mockImplementation((url: string) => {
    if (url.includes("/documents/upload-url"))
      return Promise.resolve({
        upload_token: "tok",
        signed_url: "https://storage.example/session",
        gcs_uri: "gs://wb/x",
        storage_uri: "gs://wb/x",
      });
    return Promise.resolve({ id: 1 });
  });
  render(<LabDocumentBrowser />);
  await waitFor(() => screen.getByText("No documents found."));
  fireEvent.click(screen.getByRole("button", { name: /upload document/i }));
  const file = new File(["hello"], "manual.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText(/document file/i), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: /^upload$/i }));
  await waitFor(() => {
    expect(mockPost).toHaveBeenCalledWith(
      "/api/lab-knowledge/documents/upload-url",
      expect.objectContaining({ file_name: "manual.pdf", size_bytes: 5 }),
    );
  });
  await waitFor(() => {
    expect(global.fetch as jest.Mock).toHaveBeenCalledWith(
      "https://storage.example/session",
      expect.objectContaining({ method: "PUT" }),
    );
  });
  global.fetch = realFetch;
});

test("URL import enqueues a job and polls it to completion", async () => {
  mockPost.mockResolvedValue({ id: 99, status: "pending", document_id: null, error_message: null });
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/document-tags")) return Promise.resolve([{ id: 7, name: "manual" }]);
    if (url.includes("/url-imports/99"))
      return Promise.resolve({ id: 99, status: "complete", document_id: 5, error_message: null });
    return Promise.resolve({ documents: [], total: 0, page: 1, page_size: 25 });
  });
  render(<LabDocumentBrowser />);
  await waitFor(() => screen.getByText("No documents found."));
  fireEvent.click(screen.getByRole("button", { name: /upload document/i }));
  fireEvent.click(screen.getByRole("button", { name: /from url/i }));
  fireEvent.change(screen.getByLabelText(/document url/i), {
    target: { value: "https://example.com/policy.pdf" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^import$/i }));
  await waitFor(() => {
    expect(mockPost).toHaveBeenCalledWith(
      "/api/lab-knowledge/documents/import-url",
      expect.objectContaining({ url: "https://example.com/policy.pdf" }),
    );
  });
  // It polls the import job status until it completes.
  await waitFor(() => {
    expect(mockGet.mock.calls.some(([u]) => u.includes("/url-imports/99"))).toBe(true);
  });
});
