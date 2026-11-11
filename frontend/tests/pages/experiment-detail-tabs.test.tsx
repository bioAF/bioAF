import { render, screen, fireEvent, waitFor, within } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import ExperimentDetailPage from "@/app/(app)/experiments/[id]/page";

let mockTabParam: string | null = null;
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useParams: () => ({ id: "1" }),
  useSearchParams: () => ({ get: (k: string) => (k === "tab" ? mockTabParam : null) }),
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
      storage_uri: "gs://bucket/sample_R1.fastq.gz",
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
      storage_uri: "gs://bucket/counts.h5ad",
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
  mockTabParam = null;
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === "/api/experiments/1") return Promise.resolve(mockExperiment);
    if (path.startsWith("/api/naming-profiles")) return Promise.resolve([]);
    if (path.endsWith("-batches")) return Promise.resolve([]);
    if (path.startsWith("/api/vocabularies")) return Promise.resolve({ field_name: "x", values: [] });
    if (path.includes("/api/files") && path.includes("experiment_id=1"))
      return Promise.resolve(mockFiles);
    if (path.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (path.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    return Promise.resolve({ entries: [], total: 0, files: [], page: 1, page_size: 25 });
  });
});

/**
 * The experiment detail tab strip: a named tablist whose selected tab labels
 * the one panel on screen. Arrow keys, Home and End move focus between tabs;
 * Enter or Space selects. `?tab=` picks the first tab, as before.
 *
 * Characterized before the change and preserved: only the selected tab's body
 * is mounted, and state the page owns (the overview edit form) survives a
 * round trip to another tab.
 */

/** Before the tablist existed the tabs were plain buttons; this finds either. */
function tab(name: RegExp | string) {
  return screen.queryByRole("tab", { name }) ?? screen.getByRole("button", { name });
}

async function ready() {
  await waitFor(() => screen.getByText("Test Experiment"));
}

describe("experiment tabs: characterized behavior", () => {
  it("opens the tab named by ?tab=", async () => {
    mockTabParam = "files";
    render(<ExperimentDetailPage />);
    await ready();
    await waitFor(() => expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument());
  });

  it("falls back to the overview for an unknown ?tab=", async () => {
    mockTabParam = "nonsense";
    render(<ExperimentDetailPage />);
    await ready();
    expect(screen.getByText("Experiment Details")).toBeInTheDocument();
  });

  it("mounts only the selected tab's body", async () => {
    render(<ExperimentDetailPage />);
    await ready();
    expect(screen.getByText("Experiment Details")).toBeInTheDocument();
    fireEvent.click(tab(/^files/i));
    await waitFor(() => expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument());
    expect(screen.queryByText("Experiment Details")).toBeNull();
    fireEvent.click(tab(/^overview/i));
    expect(screen.queryByText("sample_R1.fastq.gz")).toBeNull();
  });

  it("keeps an unsaved overview edit across a trip to another tab", async () => {
    render(<ExperimentDetailPage />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Renamed draft" } });
    fireEvent.click(tab(/^files/i));
    await waitFor(() => expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument());
    fireEvent.click(tab(/^overview/i));
    expect(screen.getByLabelText("Name")).toHaveValue("Renamed draft");
  });
});

describe("experiment tabs: tablist semantics", () => {
  it("is a named tablist with the selected tab labelling the panel", async () => {
    render(<ExperimentDetailPage />);
    await ready();
    const list = screen.getByRole("tablist", { name: "Experiment sections" });
    const tabs = within(list).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual([
      "Overview", "Samples (0)", "Batches", "Files", "Literature", "Analysis",
      "Pipeline Runs", "Results", "Provenance", "Audit Trail", "AI Review",
    ]);
    const selected = screen.getByRole("tab", { selected: true });
    expect(selected).toHaveTextContent("Overview");
    expect(selected).toHaveAttribute("tabindex", "0");
    expect(tabs.filter((t) => t.getAttribute("tabindex") === "-1")).toHaveLength(tabs.length - 1);
    const panel = screen.getByRole("tabpanel", { name: "Overview" });
    expect(selected).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveTextContent("Experiment Details");
  });

  it("moves focus with arrows, Home and End, and selects with Enter", async () => {
    render(<ExperimentDetailPage />);
    await ready();
    screen.getByRole("tab", { name: "Overview" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Samples (0)" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "AI Review" })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "AI Review" })).toHaveFocus();
    await userEvent.keyboard("{Home}{ArrowRight}{ArrowRight}{ArrowRight}{Enter}");
    expect(screen.getByRole("tab", { name: "Files" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument());
    expect(screen.getByRole("tabpanel", { name: "Files" })).toBeInTheDocument();
  });

  it("selects with Space", async () => {
    render(<ExperimentDetailPage />);
    await ready();
    screen.getByRole("tab", { name: "Overview" }).focus();
    await userEvent.keyboard("{ArrowRight}{ArrowRight}[Space]");
    expect(screen.getByRole("tab", { name: "Batches" })).toHaveAttribute("aria-selected", "true");
  });
});
