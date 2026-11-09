/**
 * The lab-document list and URL import. A late answer for an earlier tag/archive/query
 * selection must not replace the current list, a failed load is not "No documents found", and
 * the URL-import watch stops when its dialog closes, keeping its deadline message.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() } }));

import { LabDocumentBrowser } from "./LabDocumentBrowser";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

const doc = (id: number, title: string) => ({
  id, title, description: null, file_name: "f.pdf", current_version: 1, file_size_bytes: 1,
  mime_type: "application/pdf", md5_checksum: "x", is_archived: false, tags: [], created_by: null,
  created_at: "2026-01-15T10:00:00Z", updated_at: "2026-01-15T10:00:00Z",
});

async function answer(fragment: string, titles: string[]) {
  const i = pending.findIndex((p) => p.url.includes(fragment));
  if (i < 0) throw new Error(`no pending request for ${fragment}`);
  const [p] = pending.splice(i, 1);
  await act(async () => {
    p.resolve({ documents: titles.map((t, n) => doc(n + 1, t)), total: titles.length });
  });
}

function importCalls() {
  return mockGet.mock.calls.filter(([u]) => String(u).includes("/url-imports/")).length;
}

async function startUrlImport() {
  fireEvent.click(screen.getByRole("button", { name: /upload document/i }));
  fireEvent.click(screen.getByRole("button", { name: /from url/i }));
  fireEvent.change(screen.getByLabelText(/document url/i), { target: { value: "https://example.com/p.pdf" } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /^import$/i }));
  });
}

beforeEach(() => {
  pending = [];
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/document-tags")) return Promise.resolve([]);
    if (url.includes("/url-imports/")) return Promise.resolve({ id: 99, status: "running", document_id: null, error_message: null });
    return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
  });
});

afterEach(() => jest.useRealTimers());

test("an earlier selection's late answer does not replace the current list", async () => {
  render(<LabDocumentBrowser />);
  fireEvent.click(screen.getByLabelText(/show archived/i));

  await answer("include_archived=true", ["Archived manual"]);
  await answer("/documents?", ["Current manual"]);

  expect(screen.getByText("Archived manual")).toBeInTheDocument();
  expect(screen.queryByText("Current manual")).not.toBeInTheDocument();
});

test("a failed load is not reported as no documents", async () => {
  render(<LabDocumentBrowser />);
  const [p] = pending.splice(0, 1);
  await act(async () => {
    p.reject(new Error("Documents are unavailable."));
  });

  expect(screen.getByText("Documents are unavailable.")).toBeInTheDocument();
  expect(screen.queryByText("No documents found.")).not.toBeInTheDocument();
});

test("closing the dialog stops watching the URL import", async () => {
  jest.useFakeTimers();
  mockPost.mockResolvedValue({ id: 99, status: "pending", document_id: null, error_message: null });
  render(<LabDocumentBrowser />);
  await answer("/documents?", []);
  await startUrlImport();
  await act(async () => {
    jest.advanceTimersByTime(800);
  });
  const watched = importCalls();
  expect(watched).toBeGreaterThan(0);

  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await act(async () => {
    jest.advanceTimersByTime(60_000);
  });

  expect(importCalls()).toBe(watched);
});

test("an import still running at the deadline says it will appear later", async () => {
  jest.useFakeTimers();
  mockPost.mockResolvedValue({ id: 99, status: "pending", document_id: null, error_message: null });
  render(<LabDocumentBrowser />);
  await answer("/documents?", []);
  await startUrlImport();

  for (let i = 0; i < 120; i++) {
    await act(async () => {
      jest.advanceTimersByTime(800);
    });
  }

  expect(screen.getByText(/taking longer than expected/i)).toBeInTheDocument();
});
