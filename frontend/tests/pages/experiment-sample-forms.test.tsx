import { act, render, screen, waitFor, within } from "@/testing/renderWithProviders";
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

const SAMPLE = {
  id: 7, external_id: "S-7", organism: "Mus musculus", tissue_type: null, donor_source: null,
  treatment_condition: null, chemistry_version: null, sample_batch: null, sequencing_batch: null,
  sequencing_batch_position: null, viability_pct: null, cell_count: 0, prep_notes: null,
  molecule_type: null, library_prep_method: null, library_layout: null, assay: null,
  qc_status: null, qc_notes: null, file_count: 0, status: "registered",
  created_at: "2026-03-10T00:00:00Z", updated_at: "2026-03-10T00:00:00Z",
  custom_fields: [{ field_name: "Plate", field_value: "P1" }, { field_name: "Well", field_value: "A3" }],
};

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
  custom_fields: [
    { id: 1, field_name: "Plate", field_value: null, field_type: "text", is_required: true },
    { id: 2, field_name: "Well", field_value: null, field_type: "text", is_required: false },
  ],
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
const mockPost = jest.fn();
const mockPatch = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: jest.fn(),
  },
  fileContentUrl: (fileId: number) => `http://localhost:8000/api/files/${fileId}/content?token=fake`,
}));

beforeEach(() => {
  mockTabParam = null;
  mockPost.mockReset();
  mockPatch.mockReset();
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === "/api/experiments/1") return Promise.resolve(mockExperiment);
    if (path.startsWith("/api/naming-profiles")) return Promise.resolve([]);
    if (path.endsWith("-batches")) return Promise.resolve([]);
    if (path === "/api/experiments/1/samples") return Promise.resolve([SAMPLE]);
    if (path.startsWith("/api/vocabularies")) return Promise.resolve({ field_name: "x", values: [] });
    if (path.includes("/api/files") && path.includes("experiment_id=1"))
      return Promise.resolve(mockFiles);
    if (path.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (path.includes("/api/experiments")) return Promise.resolve({ experiments: [] });
    return Promise.resolve({ entries: [], total: 0, files: [], page: 1, page_size: 25 });
  });
});

/**
 * Sample add, edit and bulk edit, and sample-batch creation on experiment
 * detail. Each is a form: visible labels, the browser's numeric constraints,
 * Enter or the button submits once, and a failure is announced next to it.
 * Parsing stays the page's: a blank number is null and zero is zero.
 */
const { toastMock } = jest.requireMock("@/components/shared/Toast");

async function openSamples() {
  mockTabParam = "samples";
  render(<ExperimentDetailPage />);
  await waitFor(() => screen.getByText("S-7"));
}

async function openAddSample() {
  await openSamples();
  await userEvent.click(screen.getByRole("button", { name: "Add Sample" }));
  return screen.getByRole("form", { name: "Add sample" });
}

describe("add sample", () => {
  it("labels every field and keeps numeric constraints", async () => {
    const form = await openAddSample();
    for (const label of ["External Sample ID", "Organism", "Tissue Type", "Donor ID", "Treatment Condition",
      "Chemistry Version", "Sample Batch", "Sequencing Batch", "Plate *", "Well"]) {
      expect(within(form).getByLabelText(label)).toBeInTheDocument();
    }
    const cells = within(form).getByLabelText("Cell Count");
    expect(cells).toHaveAttribute("type", "number");
    expect(cells).toHaveAttribute("min", "0");
    const viability = within(form).getByLabelText("Viability %");
    expect(viability).toHaveAttribute("min", "0");
    expect(viability).toHaveAttribute("max", "100");
    expect(viability).toHaveAttribute("step", "0.1");
  });

  it("submits once from Enter, with zero kept as zero and blanks left out", async () => {
    mockPost.mockResolvedValue({});
    const form = await openAddSample();
    await userEvent.type(within(form).getByLabelText("External Sample ID"), "S-8");
    await userEvent.type(within(form).getByLabelText("Cell Count"), "0");
    await userEvent.type(within(form).getByLabelText("Plate *"), "P2");
    await userEvent.type(within(form).getByLabelText("Organism"), "Homo sapiens{Enter}");
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith("/api/experiments/1/samples", {
      external_id: "S-8",
      organism: "Homo sapiens",
      cell_count: 0,
      custom_fields: [{ field_name: "Plate", field_value: "P2" }],
    });
  });

  it("creates one sample from two submits before a re-render", async () => {
    let resolve: (v: unknown) => void = () => {};
    mockPost.mockImplementation(() => new Promise((r) => (resolve = r)));
    const form = await openAddSample();
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(mockPost).toHaveBeenCalledTimes(1);
    resolve({});
    await waitFor(() => expect(screen.queryByRole("form", { name: "Add sample" })).toBeNull());
    expect(mockPost).toHaveBeenCalledTimes(1);
  });

  it("announces a failure and keeps the form", async () => {
    mockPost.mockRejectedValue(new Error("External ID already used in this experiment."));
    const form = await openAddSample();
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("External ID already used in this experiment.");
  });

  it("Cancel closes without saving", async () => {
    const form = await openAddSample();
    await userEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(mockPost).not.toHaveBeenCalled();
    expect(screen.queryByRole("form", { name: "Add sample" })).toBeNull();
  });
});

describe("edit sample", () => {
  async function openEdit() {
    await openSamples();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    return screen.getByRole("dialog", { name: "Edit Sample" });
  }

  it("labels each custom field with its own name", async () => {
    const dialog = await openEdit();
    expect(within(dialog).getByLabelText("Plate *")).toHaveValue("P1");
    expect(within(dialog).getByLabelText("Well")).toHaveValue("A3");
    expect(within(dialog).getByLabelText("Cell Count")).toHaveValue(0);
  });

  it("saves from the footer button with the edited values", async () => {
    mockPatch.mockResolvedValue({});
    const dialog = await openEdit();
    await userEvent.clear(within(dialog).getByLabelText("Well"));
    await userEvent.type(within(dialog).getByLabelText("Well"), "B4");
    await userEvent.clear(within(dialog).getByLabelText("Cell Count"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    const [url, payload] = mockPatch.mock.calls[0];
    expect(url).toBe("/api/samples/7");
    expect(payload.cell_count).toBeNull();
    expect(payload.custom_fields).toEqual([
      { field_name: "Plate", field_value: "P1" },
      { field_name: "Well", field_value: "B4" },
    ]);
  });

  it("announces a failed save", async () => {
    mockPatch.mockRejectedValue(new Error("Viability must be between 0 and 100."));
    const dialog = await openEdit();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save Changes" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Viability must be between 0 and 100.");
  });
});

describe("bulk edit", () => {
  async function openBulk() {
    await openSamples();
    await userEvent.click(screen.getByLabelText("Select sample S-7"));
    await userEvent.click(screen.getByRole("button", { name: "Edit Selected (1)" }));
    return screen.getByRole("form", { name: "Bulk edit samples" });
  }

  it("refuses an empty update with an announced message", async () => {
    const form = await openBulk();
    await userEvent.click(within(form).getByRole("button", { name: "Apply to Selected" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Set at least one field to update");
    expect(mockPatch).not.toHaveBeenCalled();
  });

  it("sends only the filled fields", async () => {
    mockPatch.mockResolvedValue({});
    const form = await openBulk();
    await userEvent.type(within(form).getByLabelText("Tissue Type"), "Liver{Enter}");
    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith("/api/samples/bulk/update", { sample_ids: [7], update: { tissue_type: "Liver" } }),
    );
  });
});

describe("create sample batch", () => {
  async function openBatch() {
    mockTabParam = "batches";
    render(<ExperimentDetailPage />);
    await waitFor(() => screen.getByText("Sample Batches"));
    await userEvent.click(screen.getByRole("button", { name: "Create Sample Batch" }));
    return screen.getByRole("form", { name: "Create sample batch" });
  }

  it("labels its fields and saves", async () => {
    mockPost.mockResolvedValue({});
    const form = await openBatch();
    expect(within(form).getByLabelText("Prep Date")).toHaveAttribute("type", "date");
    await userEvent.type(within(form).getByLabelText("Notes"), "first run");
    await userEvent.type(within(form).getByLabelText("Batch Name *"), "B1{Enter}");
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/experiments/1/sample-batches", { name: "B1", notes: "first run" }),
    );
  });

  it("keeps reporting a failure through the toast", async () => {
    mockPost.mockRejectedValue(new Error("A batch with this name exists."));
    const form = await openBatch();
    await userEvent.type(within(form).getByLabelText("Batch Name *"), "B1");
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("A batch with this name exists."));
  });
});
