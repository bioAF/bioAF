import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { GeoExportModal } from "@/components/experiments/GeoExportModal";

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
const mockApiDownload = jest.fn();
jest.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
      public code?: string,
      public details?: Record<string, unknown>,
    ) {
      super(message);
    }
  }
  return {
    ApiError,
    api: {
      get: (...args: unknown[]) => mockApiGet(...args),
      post: (...args: unknown[]) => mockApiPost(...args),
      download: (...args: unknown[]) => mockApiDownload(...args),
    },
  };
});

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
    mockApiDownload.mockReset();
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

  it("checks readiness without failed samples when exclude-failed is checked", async () => {
    mockApiPost.mockResolvedValue(mockValidationReport);
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));
    fireEvent.click(screen.getByRole("button", { name: "Check Readiness" }));
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        "/api/experiments/1/export/geo?validate_only=true&pipeline_run_id=10&qc_status_filter=exclude_failed"
      );
    });
  });

  it("checks readiness with every sample when exclude-failed is unchecked", async () => {
    mockApiPost.mockResolvedValue(mockValidationReport);
    render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
    await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));
    fireEvent.click(screen.getByLabelText("Exclude failed samples"));
    fireEvent.click(screen.getByRole("button", { name: "Check Readiness" }));
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        "/api/experiments/1/export/geo?validate_only=true&pipeline_run_id=10&qc_status_filter=include_all"
      );
    });
  });

  describe("file association findings", () => {
    const FINGERPRINT = "f".repeat(64);
    const finding = (acknowledged: boolean) => ({
      file_id: 7,
      filename: "LIB10_R1.fastq.gz",
      file_kind: "raw",
      samples: [
        { sample_id: 1, external_id: "LIB1", qc_status: "pass", acknowledged },
        { sample_id: 2, external_id: "LIB10", qc_status: null, acknowledged },
      ],
      resolved: acknowledged,
      fingerprint: FINGERPRINT,
    });
    const blocked = { ...mockValidationReport, file_association_findings: [finding(false)], download_blocked: true };
    const resolved = { ...mockValidationReport, file_association_findings: [finding(true)], download_blocked: false };
    const READINESS = "/api/experiments/1/export/geo?validate_only=true&pipeline_run_id=10&qc_status_filter=exclude_failed";

    async function checkReadiness() {
      render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
      await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));
      fireEvent.click(screen.getByRole("button", { name: "Check Readiness" }));
      await screen.findByText("LIB10_R1.fastq.gz");
    }

    it("lists each finding with its matched samples and blocks download", async () => {
      mockApiPost.mockResolvedValue(blocked);
      await checkReadiness();
      expect(screen.getByText(/LIB1, LIB10/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Download Export/ })).toBeDisabled();
    });

    it("links the file to a chosen sample, then checks readiness again", async () => {
      mockApiPost.mockResolvedValueOnce(blocked).mockResolvedValueOnce({ status: "linked" }).mockResolvedValueOnce(mockValidationReport);
      await checkReadiness();
      fireEvent.click(screen.getByRole("button", { name: "Link to LIB10" }));

      await waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(3));
      expect(mockApiPost).toHaveBeenNthCalledWith(2, "/api/files/7/link", { sample_id: 2 });
      expect(mockApiPost).toHaveBeenNthCalledWith(3, READINESS);
      await waitFor(() => expect(screen.queryByText("LIB10_R1.fastq.gz")).not.toBeInTheDocument());
      expect(screen.getByRole("button", { name: /Download Export/ })).toBeEnabled();
    });

    it("keeps the file at experiment level with a reason, then checks readiness again", async () => {
      mockApiPost.mockResolvedValueOnce(blocked).mockResolvedValueOnce({ status: "acknowledged" }).mockResolvedValueOnce(resolved);
      await checkReadiness();
      fireEvent.click(screen.getByRole("button", { name: "Keep at experiment level" }));
      const confirm = screen.getByRole("button", { name: "Confirm" });
      expect(confirm).toBeDisabled();
      fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "Lane file shared by both libraries" } });
      fireEvent.click(confirm);

      await waitFor(() => expect(mockApiPost).toHaveBeenCalledTimes(3));
      expect(mockApiPost).toHaveBeenNthCalledWith(
        2,
        "/api/experiments/1/export/geo/file-associations/acknowledgments",
        { file_id: 7, sample_ids: [1, 2], fingerprint: FINGERPRINT, reason: "Lane file shared by both libraries" },
      );
      expect(mockApiPost).toHaveBeenNthCalledWith(3, READINESS);
      expect(await screen.findByText("Resolved")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Download Export/ })).toBeEnabled();
    });

    it("shows why a resolution was refused and keeps download blocked", async () => {
      mockApiPost.mockResolvedValueOnce(blocked).mockRejectedValueOnce(new Error("You do not have permission to edit files."));
      await checkReadiness();
      fireEvent.click(screen.getByRole("button", { name: "Link to LIB1" }));
      expect(await screen.findByText("You do not have permission to edit files.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Download Export/ })).toBeDisabled();
    });

    it("clears a displayed report when the QC filter changes", async () => {
      mockApiPost.mockResolvedValue(blocked);
      await checkReadiness();
      fireEvent.click(screen.getByLabelText("Exclude failed samples"));
      expect(screen.queryByText("LIB10_R1.fastq.gz")).not.toBeInTheDocument();
      expect(screen.queryByText("Validation Summary")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Download Export/ })).toBeEnabled();
    });

    it("clears a displayed report when the pipeline run changes", async () => {
      mockApiPost.mockResolvedValue(blocked);
      await checkReadiness();
      fireEvent.change(screen.getByLabelText("Pipeline Run"), { target: { value: "" } });
      expect(screen.queryByText("Validation Summary")).not.toBeInTheDocument();
    });

    it("shows the findings when the server refuses a download over them", async () => {
      const { ApiError } = jest.requireMock("@/lib/api");
      mockApiDownload.mockRejectedValue(
        new ApiError(409, "Some files may have lost their Sample references.", "geo_file_associations_unresolved", {}),
      );
      mockApiPost.mockResolvedValue(blocked);
      render(<GeoExportModal experimentId={1} isOpen onClose={jest.fn()} userRole="admin" />);
      await waitFor(() => screen.getByText(/nf-core\/scrnaseq/));
      fireEvent.click(screen.getByRole("button", { name: /Download Export/ }));

      expect(await screen.findByText("Some files may have lost their Sample references.")).toBeInTheDocument();
      expect(await screen.findByText("LIB10_R1.fastq.gz")).toBeInTheDocument();
      expect(mockApiPost).toHaveBeenCalledWith(READINESS);
    });
  });
});
