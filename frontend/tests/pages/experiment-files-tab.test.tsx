import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ExperimentDetailPage from "@/app/experiments/[id]/page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useParams: () => ({ id: "1" }),
}));

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <div data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <div data-testid="header" />,
}));
jest.mock("@/components/experiments/ExperimentStatusBadge", () => ({
  ExperimentStatusBadge: () => <span data-testid="status-badge" />,
}));
jest.mock("@/components/experiments/SampleQCBadge", () => ({
  SampleQCBadge: () => <span />,
}));
jest.mock("@/components/experiments/GeoExportModal", () => ({
  GeoExportModal: () => null,
}));
jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="loading" />,
}));
jest.mock("@/components/shared/ContentLoading", () => ({
  ContentLoading: () => <div data-testid="content-loading" />,
}));
jest.mock("@/components/shared/VocabularySelect", () => ({
  VocabularySelect: () => <select />,
}));
jest.mock("@/components/SnapshotTimeline", () => ({
  __esModule: true,
  default: () => <div />,
}));
jest.mock("@/components/provenance/ProvenanceReportPanel", () => ({
  ProvenanceReportPanel: () => <div data-testid="provenance-panel" />,
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true }),
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getToken: () => "test-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin" }),
}));

const mockExperiment = {
  id: 1,
  name: "Test Experiment",
  status: "registered",
  hypothesis: null,
  description: null,
  start_date: null,
  expected_sample_count: null,
  project: null,
  template_id: null,
  template_name: null,
  owner: { id: 1, name: "Admin", email: "admin@test.com" },
  sample_count: 0,
  batch_count: 0,
  samples: [],
  batches: [],
  custom_fields: [],
  field_defaults: [],
  audit_trail_count: 0,
  created_at: "2026-03-10T00:00:00Z",
  updated_at: "2026-03-10T00:00:00Z",
};

const mockFiles = {
  files: [
    {
      id: 101,
      filename: "sample_R1.fastq.gz",
      gcs_uri: "gs://bucket/sample_R1.fastq.gz",
      size_bytes: 3221225472,
      md5_checksum: "abc",
      file_type: "fastq",
      tags: [],
      uploader: { id: 1, name: "Admin", email: "admin@test.com" },
      experiment_id: 1,
      project_id: null,
      sample_ids: [],
      source_type: "upload",
      source_pipeline_run_id: null,
      upload_timestamp: "2026-03-12T10:00:00Z",
      created_at: "2026-03-12T10:00:00Z",
    },
    {
      id: 102,
      filename: "counts.h5ad",
      gcs_uri: "gs://bucket/counts.h5ad",
      size_bytes: 52428800,
      md5_checksum: "def",
      file_type: "h5ad",
      tags: [],
      uploader: { id: 1, name: "Admin", email: "admin@test.com" },
      experiment_id: 1,
      project_id: null,
      sample_ids: [],
      source_type: "upload",
      source_pipeline_run_id: null,
      upload_timestamp: "2026-03-13T14:00:00Z",
      created_at: "2026-03-13T14:00:00Z",
    },
  ],
  total: 2,
  page: 1,
  page_size: 25,
};

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  fileContentUrl: (fileId: number) => `http://localhost:8000/api/files/${fileId}/content?token=fake`,
}));

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === "/api/experiments/1") return Promise.resolve(mockExperiment);
    if (path.includes("/api/files") && path.includes("experiment_id=1"))
      return Promise.resolve(mockFiles);
    if (path.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (path.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    return Promise.resolve({ entries: [], total: 0, files: [], page: 1, page_size: 25 });
  });
});

describe("Experiment Detail - Files Tab", () => {
  it("renders a Files tab button", async () => {
    render(<ExperimentDetailPage />);
    await waitFor(() => screen.getByText("Test Experiment"));
    expect(screen.getByRole("button", { name: /files/i })).toBeInTheDocument();
  });

  it("fetches and displays files when Files tab is clicked", async () => {
    render(<ExperimentDetailPage />);
    await waitFor(() => screen.getByText("Test Experiment"));

    fireEvent.click(screen.getByRole("button", { name: /files/i }));

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        expect.stringContaining("experiment_id=1"),
      );
    });

    expect(await screen.findByText("sample_R1.fastq.gz")).toBeInTheDocument();
    expect(screen.getByText("counts.h5ad")).toBeInTheDocument();
  });

  it("shows file type, uploader, and upload date", async () => {
    render(<ExperimentDetailPage />);
    await waitFor(() => screen.getByText("Test Experiment"));

    fireEvent.click(screen.getByRole("button", { name: /files/i }));

    await waitFor(() => screen.getByText("sample_R1.fastq.gz"));
    expect(screen.getAllByText("fastq").length).toBeGreaterThan(0);
    expect(screen.getAllByText("h5ad").length).toBeGreaterThan(0);
  });

  it("shows empty state when no files exist", async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === "/api/experiments/1") return Promise.resolve(mockExperiment);
      if (path.includes("/api/projects")) return Promise.resolve({ projects: [] });
      if (path.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
      return Promise.resolve({ files: [], total: 0, page: 1, page_size: 25 });
    });

    render(<ExperimentDetailPage />);
    await waitFor(() => screen.getByText("Test Experiment"));

    fireEvent.click(screen.getByRole("button", { name: /files/i }));

    await waitFor(() => {
      expect(screen.getByText("No files found.")).toBeInTheDocument();
    });
  });
});
