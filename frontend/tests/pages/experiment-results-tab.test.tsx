import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ExperimentDetailPage from "@/app/(app)/experiments/[id]/page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useParams: () => ({ id: "1" }),
  useSearchParams: () => ({ get: () => null }),
}));

jest.mock("@/components/experiments/ExperimentStatusBadge", () => ({
  ExperimentStatusBadge: () => <span />,
}));
jest.mock("@/components/experiments/SampleQCBadge", () => ({ SampleQCBadge: () => <span /> }));
jest.mock("@/components/experiments/GeoExportModal", () => ({ GeoExportModal: () => null }));
jest.mock("@/components/shared/LoadingSpinner", () => ({ LoadingSpinner: () => <div /> }));
jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => <div /> }));
jest.mock("@/components/shared/VocabularySelect", () => ({ VocabularySelect: () => <select /> }));
jest.mock("@/components/SnapshotTimeline", () => ({ __esModule: true, default: () => <div /> }));
jest.mock("@/components/provenance/ProvenanceReportPanel", () => ({
  ProvenanceReportPanel: () => <div />,
}));
const mockCanAccess = jest.fn(() => true);
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: mockCanAccess }) }));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getToken: () => "test-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin" }),
}));

// The real QC report modal is exercised in its own test; here we only verify
// the Results tab opens it for the clicked dashboard.
jest.mock("@/components/qc/QCReportModal", () => ({
  QCReportModal: (props: { dashboardId: number }) => (
    <div data-testid="qc-report-modal-stub">dash:{props.dashboardId}</div>
  ),
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

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  fileContentUrl: (fileId: number) => `http://localhost:8000/api/files/${fileId}/content`,
  plotThumbnailContentUrl: (id: number) => `http://localhost:8000/api/plots/${id}/thumbnail`,
}));

jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: (id: number | null) => (id ? `file-url-${id}` : null),
  usePlotThumbnailContentUrl: (id: number | null) => (id ? `thumb-url-${id}` : null),
}));

beforeEach(() => {
  mockCanAccess.mockReturnValue(true);
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === "/api/experiments/1") return Promise.resolve(mockExperiment);
    if (path.includes("/api/qc-dashboards") && path.includes("experiment_id=1")) {
      return Promise.resolve([
        {
          id: 9,
          pipeline_run_id: 42,
          quality_rating: "good",
          cell_count: 5000,
          status: "ready",
          generated_at: "2026-05-14T00:00:00Z",
          project_name: "Project Alpha",
          experiment_name: "Alpha Exp 1",
          pipeline_name: "nf-core/scrnaseq",
          pipeline_version: "2.6.0",
          sample_external_ids: ["SAMPLE-001"],
        },
      ]);
    }
    if (path.includes("/api/cellxgene")) return Promise.resolve([]);
    if (path.includes("/api/plots")) {
      return Promise.resolve({
        plots: [
          {
            id: 1,
            title: "UMAP plot",
            file: { id: 7, file_type: "png", storage_deleted: false },
            experiment_id: 1,
            experiment_name: "Alpha Exp 1",
            project_name: "Project Alpha",
            pipeline_run_id: 42,
            pipeline_run_name: null,
            notebook_session_id: null,
            notebook_session_type: null,
            source_type: "pipeline",
            tags: [],
            thumbnail_url: null,
            indexed_at: "2026-05-14T00:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        page_size: 12,
      });
    }
    if (path.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (path.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    return Promise.resolve({ entries: [], total: 0, files: [], page: 1, page_size: 25 });
  });
});

describe("Experiment Detail - Results Tab", () => {
  it("lists QC dashboards and opens the real QC report modal on click", async () => {
    render(<ExperimentDetailPage />);
    await waitFor(() => screen.getByText("Test Experiment"));

    fireEvent.click(screen.getByRole("button", { name: "Results" }));

    expect(await screen.findByText("Run #42")).toBeInTheDocument();
    // The old inline custom detail (Back to list / Regenerate) is gone.
    expect(screen.queryByText("Back to list")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Run #42"));

    expect(await screen.findByTestId("qc-report-modal-stub")).toHaveTextContent("dash:9");
  });

  it("shows rich QC cards and working plot previews matching the canonical pages", async () => {
    render(<ExperimentDetailPage />);
    await waitFor(() => screen.getByText("Test Experiment"));

    fireEvent.click(screen.getByRole("button", { name: "Results" }));

    // QC card carries the same context the Results > QC Dashboards page shows.
    expect(await screen.findByText(/nf-core\/scrnaseq v2\.6\.0/)).toBeInTheDocument();
    expect(screen.getByText("Project Alpha")).toBeInTheDocument();
    expect(screen.getByText("Alpha Exp 1")).toBeInTheDocument();

    // Plot preview renders a real image rather than failing to load.
    const img = (await screen.findByAltText("UMAP plot")) as HTMLImageElement;
    expect(img.src).toContain("file-url-7");
  });

  it("hides the Results tab when the user cannot view Results", async () => {
    mockCanAccess.mockReturnValue(false);
    render(<ExperimentDetailPage />);
    await waitFor(() => screen.getByText("Test Experiment"));
    expect(screen.queryByRole("button", { name: "Results" })).not.toBeInTheDocument();
  });
});
