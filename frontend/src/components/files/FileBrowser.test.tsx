import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { FileBrowser } from "./FileBrowser";

jest.mock("@/components/shared/ContentLoading", () => ({
  ContentLoading: () => <div data-testid="loading" />,
}));

jest.mock("@/components/provenance/ProvenanceReportPanel", () => ({
  ProvenanceReportPanel: () => <div data-testid="provenance-panel" />,
}));

jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ role_name: "admin" }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true }),
}));

jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    delete: jest.fn(),
  },
  fileContentUrl: (id: number) => Promise.resolve(`/api/files/${id}/content`),
}));

jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: (id: number | null) =>
    id != null ? `/api/files/${id}/content` : null,
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

const makeFile = (overrides = {}) => ({
  id: 1,
  filename: "sample.fastq.gz",
  gcs_uri: "gs://bucket/sample.fastq.gz",
  size_bytes: 1048576,
  md5_checksum: "abc123",
  file_type: "fastq",
  tags: [],
  uploader: { id: 1, name: "Alice", email: "alice@example.com" },
  upload_timestamp: "2026-01-15T10:00:00Z",
  created_at: "2026-01-15T10:00:00Z",
  source_type: "upload",
  source_pipeline_run_id: null,
  experiment_id: null,
  project_id: null,
  sample_ids: [],
  ...overrides,
});

beforeEach(() => {
  mockGet.mockReset();
  // Default: return empty lists for meta calls
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    return Promise.resolve({ files: [], total: 0, page: 1, page_size: 25 });
  });
});

test("renders loading state initially", () => {
  mockGet.mockImplementation(() => new Promise(() => {}));
  render(<FileBrowser />);
  expect(screen.getByTestId("loading")).toBeInTheDocument();
});

test("renders empty state when no files", async () => {
  render(<FileBrowser />);
  await waitFor(() => {
    expect(screen.getByText("No files found.")).toBeInTheDocument();
  });
});

test("renders file rows when files are returned", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    return Promise.resolve({
      files: [makeFile()],
      total: 1,
      page: 1,
      page_size: 25,
    });
  });

  render(<FileBrowser />);

  await waitFor(() => {
    expect(screen.getByText("sample.fastq.gz")).toBeInTheDocument();
  });
  expect(screen.getAllByText("fastq").length).toBeGreaterThan(0);
  expect(screen.getByText("1.0 MB")).toBeInTheDocument();
});

test("passes experiment_id filter to API", async () => {
  render(<FileBrowser experimentId={42} />);

  await waitFor(() => {
    const calls = mockGet.mock.calls.map(([url]: [string]) => url);
    expect(calls.some((url: string) => url.includes("experiment_id=42"))).toBe(true);
  });
});

test("passes project_id filter to API", async () => {
  render(<FileBrowser projectId={7} />);

  await waitFor(() => {
    const calls = mockGet.mock.calls.map(([url]: [string]) => url);
    expect(calls.some((url: string) => url.includes("project_id=7"))).toBe(true);
  });
});

test("shows Unlinked badge for files with no association", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    return Promise.resolve({
      files: [makeFile({ experiment_id: null, project_id: null })],
      total: 1,
      page: 1,
      page_size: 25,
    });
  });

  render(<FileBrowser />);

  await waitFor(() => {
    expect(screen.getByText("Unlinked")).toBeInTheDocument();
  });
});

test("renders Upload toggle when showUpload + experimentId are set", async () => {
  render(<FileBrowser experimentId={42} showUpload />);
  await waitFor(() => {
    expect(screen.getByRole("button", { name: /upload/i })).toBeInTheDocument();
  });
});

test("does not render Upload toggle without experimentId", async () => {
  render(<FileBrowser showUpload />);
  await waitFor(() => {
    expect(screen.getByText("No files found.")).toBeInTheDocument();
  });
  expect(screen.queryByRole("button", { name: /upload/i })).not.toBeInTheDocument();
});

test("renders search input when showSearch is set", async () => {
  render(<FileBrowser experimentId={42} showSearch />);
  await waitFor(() => {
    expect(
      screen.getByPlaceholderText(/search by filename/i),
    ).toBeInTheDocument();
  });
});

test("focusFileId opens that file's detail directly", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    if (/\/api\/files\/\d+$/.test(url)) {
      return Promise.resolve(makeFile({ id: 77, filename: "focused.fastq.gz" }));
    }
    return Promise.resolve({ files: [], total: 0, page: 1, page_size: 25 });
  });

  render(<FileBrowser focusFileId={77} />);

  await waitFor(() => {
    // "View Provenance" only renders inside the file detail modal.
    expect(screen.getByText("View Provenance")).toBeInTheDocument();
  });
  expect(screen.getByText("focused.fastq.gz")).toBeInTheDocument();
  expect(mockGet).toHaveBeenCalledWith("/api/files/77");
});

const makeLabDocHit = (overrides = {}) => ({
  kind: "lab_document",
  id: 3,
  name: "Assay SOP",
  file_type: "application/pdf",
  size_bytes: 4096,
  updated_at: "2026-01-15T10:00:00Z",
  href: "/lab-knowledge/documents/3",
  experiment_id: null,
  source: "lab_knowledge",
  ...overrides,
});

test("search surfaces Lab Knowledge documents with a badge and detail link", async () => {
  // LK-SPEC-D D3 / AC-D10
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    if (url.includes("/api/files/search"))
      return Promise.resolve({ items: [makeLabDocHit()] });
    return Promise.resolve({ files: [makeFile()], total: 1, page: 1, page_size: 25 });
  });

  render(<FileBrowser showSearch />);
  await waitFor(() => screen.getByText("sample.fastq.gz"));

  fireEvent.change(screen.getByPlaceholderText(/search by filename/i), {
    target: { value: "assay" },
  });
  fireEvent.submit(screen.getByPlaceholderText(/search by filename/i).closest("form")!);

  await waitFor(() => {
    expect(screen.getByText("Assay SOP")).toBeInTheDocument();
  });
  expect(screen.getByText("Lab Document")).toBeInTheDocument();
  const link = screen.getByText("Assay SOP").closest("a");
  expect(link).toHaveAttribute("href", "/lab-knowledge/documents/3");
});

test("lab documents are not fetched without an active search", async () => {
  render(<FileBrowser showSearch />);
  await waitFor(() => screen.getByText("No files found."));
  const calls = mockGet.mock.calls.map(([url]: [string]) => url);
  expect(calls.some((url: string) => url.includes("/api/files/search"))).toBe(false);
});

test("renders provenance breadcrumb when API returns provenance", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/experiments"))
      return Promise.resolve({
        experiments: [{ id: 5, name: "Exp Alpha" }],
      });
    return Promise.resolve({
      files: [
        makeFile({
          experiment_id: 5,
          sample_ids: [10, 11],
          provenance: {
            project_id: null,
            project_name: null,
            experiment_id: 5,
            experiment_name: "Exp Alpha",
            sample_labels: ["S010", "S011"],
            pipeline_run: null,
            compute_session: null,
            creator: { id: 1, name: "Maria", email: "maria@test.com" },
          },
        }),
      ],
      total: 1,
      page: 1,
      page_size: 25,
    });
  });

  render(<FileBrowser />);

  await waitFor(() => {
    expect(screen.getByText("Exp Alpha › S010, S011")).toBeInTheDocument();
  });
});
