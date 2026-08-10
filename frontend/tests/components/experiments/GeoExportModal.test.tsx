import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { GeoExportModal } from "@/components/experiments/GeoExportModal";

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

jest.mock("@/lib/auth", () => ({
  getToken: () => "test-token",
}));

const mockPipelineRuns = [
  {
    id: 10,
    pipeline_name: "nf-core/scrnaseq",
    pipeline_version: "3.1.0",
    status: "completed",
    created_at: "2026-03-01T10:00:00Z",
    experiment: null,
    submitted_by: null,
    parameters: null,
    input_files: null,
    output_files: null,
    progress: null,
    cost_estimate: null,
    error_message: null,
    work_dir: null,
    slurm_job_id: null,
    reference_genome: null,
    alignment_algorithm: null,
    resume_from_run_id: null,
    review_verdict: null,
    started_at: null,
    completed_at: null,
    pipeline_key: null,
  },
];

const mockValidationReport = {
  experiment_id: 1,
  pipeline_run_id: 10,
  series_fields: [
    { geo_column: "title", status: "complete" as const, value: "My Experiment", message: null },
    { geo_column: "organism", status: "missing_required" as const, value: null, message: "Required field missing" },
  ],
  protocol_fields: [
    { geo_column: "extract_protocol", status: "populated_unvalidated" as const, value: "Standard protocol", message: null },
  ],
  sample_validations: [
    {
      sample_id: 1,
      sample_name: "Sample A",
      fields: [
        { geo_column: "source_name", status: "complete" as const, value: "Tissue", message: null },
        { geo_column: "molecule", status: "missing_recommended" as const, value: null, message: null },
      ],
    },
  ],
  summary: {
    total_fields: 4,
    complete: 2,
    populated_unvalidated: 1,
    missing_required: 1,
    missing_recommended: 1,
  },
};

describe("GeoExportModal", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPost.mockReset();
    mockApiGet.mockResolvedValue({ runs: mockPipelineRuns, total: 1, page: 1, page_size: 20 });
  });

  it("does not render when isOpen is false", () => {
    render(
      <GeoExportModal experimentId={1} isOpen={false} onClose={jest.fn()} userRole="admin" />
    );
    expect(screen.queryByText("Export to GEO")).not.toBeInTheDocument();
  });

  it("renders Export to GEO title when isOpen", async () => {
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    expect(screen.getByText("Export to GEO")).toBeInTheDocument();
  });

  it("fetches pipeline runs on open and populates selector", async () => {
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => {
      expect(mockApiGet).toHaveBeenCalledWith("/api/pipeline-runs?experiment_id=1");
      expect(screen.getByText(/nf-core\/scrnaseq/)).toBeInTheDocument();
    });
  });

  it("calls onClose when the close button is clicked", () => {
    // Was a bare "×" with no accessible name. The shared Modal shell now supplies
    // the control and labels it, so the assertion is unchanged but the button is
    // findable by a screen reader as well as by this test.
    const onClose = jest.fn();
    render(<GeoExportModal experimentId={1} isOpen onClose={onClose} userRole="admin" />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("Check Readiness button is disabled when no pipeline run selected", async () => {
    mockApiGet.mockResolvedValue({ runs: [], total: 0, page: 1, page_size: 20 });
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: "Check Readiness" });
      expect(btn).toBeDisabled();
    });
  });

  it("Check Readiness calls validation endpoint with selected run", async () => {
    mockApiPost.mockResolvedValue(mockValidationReport);
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));

    fireEvent.click(screen.getByRole("button", { name: "Check Readiness" }));

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        expect.stringContaining("/api/experiments/1/export/geo?validate_only=true&pipeline_run_id=10")
      );
    });
  });

  it("renders validation summary after Check Readiness succeeds", async () => {
    mockApiPost.mockResolvedValue(mockValidationReport);
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));

    fireEvent.click(screen.getByRole("button", { name: "Check Readiness" }));

    await waitFor(() => {
      expect(screen.getByText("Validation Summary")).toBeInTheDocument();
      expect(screen.getByText(/Complete: 2/)).toBeInTheDocument();
      expect(screen.getByText(/Missing \(Required\): 1/)).toBeInTheDocument();
    });
  });

  it("renders series fields table after validation", async () => {
    mockApiPost.mockResolvedValue(mockValidationReport);
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));
    fireEvent.click(screen.getByRole("button", { name: "Check Readiness" }));
    await waitFor(() => {
      expect(screen.getByText("Series Fields")).toBeInTheDocument();
      expect(screen.getByText("title")).toBeInTheDocument();
      expect(screen.getByText("organism")).toBeInTheDocument();
      // FieldStatusIcon: complete=OK, missing_required=X
      expect(screen.getAllByText("OK").length).toBeGreaterThan(0);
      expect(screen.getAllByText("X").length).toBeGreaterThan(0);
    });
  });

  it("renders sample validations section", async () => {
    mockApiPost.mockResolvedValue(mockValidationReport);
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));
    fireEvent.click(screen.getByRole("button", { name: "Check Readiness" }));
    await waitFor(() => {
      expect(screen.getByText(/Sample Validations/)).toBeInTheDocument();
      expect(screen.getByText("Sample A")).toBeInTheDocument();
    });
  });

  it("shows error message when validation fails", async () => {
    mockApiPost.mockRejectedValue(new Error("Validation endpoint error"));
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));
    fireEvent.click(screen.getByRole("button", { name: "Check Readiness" }));
    await waitFor(() => {
      expect(screen.getByText("Validation endpoint error")).toBeInTheDocument();
    });
  });

  it("Exclude failed samples checkbox is checked by default", async () => {
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    const checkbox = screen.getByLabelText("Exclude failed samples");
    expect(checkbox).toBeChecked();
  });

  it("adds qc_status_filter=pass to validation URL when exclude-failed is checked", async () => {
    mockApiPost.mockResolvedValue(mockValidationReport);
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));
    fireEvent.click(screen.getByRole("button", { name: "Check Readiness" }));
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        expect.stringContaining("qc_status_filter=pass")
      );
    });
  });
});
