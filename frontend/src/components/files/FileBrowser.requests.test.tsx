/**
 * The file list accepted answers in arrival order and swallowed failures, so an older search
 * could replace a newer one and an outage read as "No files found." (plan 13). The list (with its
 * lab-document hits) is keyed by the submitted query, scope, filters, and page; only the current
 * key's answer is shown; a failure says so.
 */
import { render, screen, fireEvent, act, within } from "@/testing/renderWithProviders";

jest.mock("@/components/shared/ContentLoading", () => ({
  ContentLoading: () => <div data-testid="loading" />,
}));
jest.mock("@/components/provenance/ProvenanceReportPanel", () => ({
  ProvenanceReportPanel: () => null,
}));
jest.mock("@/lib/auth", () => ({ getCurrentUser: () => ({ role_name: "admin" }) }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true }) }));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
  fileContentUrl: async () => "",
}));
jest.mock("@/hooks/useContentUrl", () => ({ useFileContentUrl: () => null }));

import { FileBrowser } from "./FileBrowser";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockDelete = api.delete as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function file(id: number, filename: string) {
  return {
    id, filename, gcs_uri: "", storage_uri: "", size_bytes: 10, md5_checksum: null, file_type: "fastq",
    tags: [], uploader: null, upload_timestamp: "2026-01-15T10:00:00Z", created_at: "2026-01-15T10:00:00Z",
    source_type: "upload", source_pipeline_run_id: null, experiment_id: null, project_id: null, sample_ids: [],
  };
}

function labDoc(id: number, name: string) {
  return {
    kind: "lab_document", id, name, file_type: "pdf", size_bytes: 1, updated_at: "2026-01-01T00:00:00Z",
    href: `/lab-knowledge/documents/${id}`, experiment_id: null, source: "lab_knowledge",
  };
}

function take(fragment: string): Pending {
  const index = pending.findIndex((p) => p.url.includes(fragment));
  if (index < 0) throw new Error(`no pending request matching ${fragment}: ${pending.map((p) => p.url)}`);
  return pending.splice(index, 1)[0];
}

function answer(fragment: string, value: unknown) {
  const p = take(fragment);
  return act(async () => {
    p.resolve(value);
  });
}

function files(names: string[]) {
  return { files: names.map((n, i) => file(i + 1, n)), total: names.length, page: 1, page_size: 25 };
}

function fail(fragment: string) {
  const p = take(fragment);
  return act(async () => {
    p.reject(new Error("502"));
  });
}

function submitSearch(value: string) {
  fireEvent.change(screen.getByLabelText("Search by filename"), { target: { value } });
  fireEvent.submit(screen.getByLabelText("Search by filename").closest("form")!);
}

beforeEach(() => {
  pending = [];
  mockGet.mockReset();
  mockDelete.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

test("an older search's late answer, files and lab documents, does not replace the newer one", async () => {
  render(<FileBrowser showSearch />);
  await answer("/api/files?", files(["a.fastq", "b.fastq"]));

  submitSearch("a");
  submitSearch("b");
  await answer("search=b", files(["b.fastq"]));
  await answer("/api/files/search?q=b", { items: [labDoc(3, "b protocol")] });
  await answer("search=a", files(["a.fastq", "aa.fastq"]));
  await answer("/api/files/search?q=a", { items: [labDoc(4, "a protocol")] });

  expect(screen.getByText("b.fastq")).toBeInTheDocument();
  expect(screen.getByText("b protocol")).toBeInTheDocument();
  expect(screen.queryByText("aa.fastq")).not.toBeInTheDocument();
  expect(screen.queryByText("a protocol")).not.toBeInTheDocument();
});

test("typing does not search until the search is submitted", async () => {
  render(<FileBrowser showSearch />);
  await answer("/api/files?", files(["a.fastq"]));
  fireEvent.change(screen.getByLabelText("Search by filename"), { target: { value: "abc" } });
  expect(pending).toHaveLength(0);
});

test("a failed load says so instead of claiming there are no files", async () => {
  render(<FileBrowser />);
  await fail("/api/files?");
  expect(screen.getByTestId("error-message")).toHaveTextContent(/files could not be loaded/i);
  expect(screen.queryByText("No files found.")).not.toBeInTheDocument();

  fireEvent.click(screen.getByTestId("error-retry"));
  await answer("/api/files?", files(["a.fastq"]));
  expect(screen.getByText("a.fastq")).toBeInTheDocument();
});

test("a failed refresh after a delete keeps the list and says it may be out of date", async () => {
  mockDelete.mockResolvedValue({});
  render(<FileBrowser />);
  await answer("/api/files?", files(["a.fastq", "b.fastq"]));

  fireEvent.click(screen.getAllByRole("checkbox")[1]);
  fireEvent.click(screen.getByText("Delete"));
  const dialog = await screen.findByRole("dialog");
  await act(async () => {
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
  });
  await fail("/api/files?");

  expect(screen.getByText("b.fastq")).toBeInTheDocument();
  expect(screen.getByTestId("stale-notice")).toHaveTextContent(/may be out of date/i);
});
