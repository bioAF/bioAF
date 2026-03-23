import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SuperSeriesExportModal } from "@/components/experiments/SuperSeriesExportModal";

jest.mock("next/navigation", () => ({
  usePathname: () => "/projects/1",
  useRouter: () => ({ push: jest.fn() }),
}));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ email: "test@bioaf.org", role_name: "comp_bio", sub: "1" }),
  getToken: () => "mock-token",
}));

jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <span data-testid="loading-spinner">loading...</span>,
}));

const mockExperiments = [
  { id: 1, name: "RNA-Seq Experiment A", status: "complete" },
  { id: 2, name: "ChIP-Seq Experiment B", status: "analysis" },
  { id: 3, name: "ATAC-Seq Experiment C", status: "sequencing" },
];

const defaultProps = {
  projectId: 42,
  experiments: mockExperiments,
  isOpen: true,
  onClose: jest.fn(),
};

describe("SuperSeriesExportModal", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPost.mockReset();
    defaultProps.onClose.mockReset();
  });

  it("opens with experiment list populated", () => {
    render(<SuperSeriesExportModal {...defaultProps} />);

    expect(screen.getByTestId("superseries-modal")).toBeInTheDocument();
    expect(screen.getByText("Export GEO SuperSeries")).toBeInTheDocument();

    // All experiments are listed
    expect(screen.getByText("RNA-Seq Experiment A")).toBeInTheDocument();
    expect(screen.getByText("ChIP-Seq Experiment B")).toBeInTheDocument();
    expect(screen.getByText("ATAC-Seq Experiment C")).toBeInTheDocument();

    // All checkboxes are checked by default
    const checkbox1 = screen.getByTestId("experiment-checkbox-1") as HTMLInputElement;
    const checkbox2 = screen.getByTestId("experiment-checkbox-2") as HTMLInputElement;
    const checkbox3 = screen.getByTestId("experiment-checkbox-3") as HTMLInputElement;
    expect(checkbox1.checked).toBe(true);
    expect(checkbox2.checked).toBe(true);
    expect(checkbox3.checked).toBe(true);

    expect(screen.getByText("3 of 3 experiments selected")).toBeInTheDocument();
  });

  it("does not render when isOpen is false", () => {
    render(<SuperSeriesExportModal {...defaultProps} isOpen={false} />);
    expect(screen.queryByTestId("superseries-modal")).not.toBeInTheDocument();
  });

  it("experiment checkboxes toggle inclusion", () => {
    render(<SuperSeriesExportModal {...defaultProps} />);

    const checkbox2 = screen.getByTestId("experiment-checkbox-2") as HTMLInputElement;
    expect(checkbox2.checked).toBe(true);

    fireEvent.click(checkbox2);
    expect(checkbox2.checked).toBe(false);
    expect(screen.getByText("2 of 3 experiments selected")).toBeInTheDocument();

    // Toggle back on
    fireEvent.click(checkbox2);
    expect(checkbox2.checked).toBe(true);
    expect(screen.getByText("3 of 3 experiments selected")).toBeInTheDocument();
  });

  it("validation summary displays warnings and errors", async () => {
    const validationResult = {
      valid: false,
      errors: [
        { level: "error", field: "organism", message: "Organism is required for all samples" },
        { level: "error", message: "Experiment 2 has no processed data" },
      ],
      warnings: [
        { level: "warning", field: "treatment", message: "Treatment protocol not specified" },
      ],
      experiment_count: 3,
      sample_count: 15,
    };
    mockApiPost.mockResolvedValueOnce(validationResult);

    render(<SuperSeriesExportModal {...defaultProps} />);

    fireEvent.click(screen.getByTestId("validate-button"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-summary")).toBeInTheDocument();
    });

    // Errors are displayed
    const errors = screen.getAllByTestId("validation-error");
    expect(errors).toHaveLength(2);
    expect(errors[0]).toHaveTextContent("organism: Organism is required for all samples");
    expect(errors[1]).toHaveTextContent("Experiment 2 has no processed data");

    // Warnings are displayed
    const warnings = screen.getAllByTestId("validation-warning");
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toHaveTextContent("treatment: Treatment protocol not specified");

    // Correct API call was made
    expect(mockApiPost).toHaveBeenCalledWith(
      "/api/projects/42/export/geo?validate_only=true",
      { experiment_ids: [1, 2, 3] },
    );
  });

  it("download button disabled when validation errors exist", async () => {
    const validationResult = {
      valid: false,
      errors: [
        { level: "error", message: "Missing required metadata" },
      ],
      warnings: [],
      experiment_count: 3,
      sample_count: 15,
    };
    mockApiPost.mockResolvedValueOnce(validationResult);

    render(<SuperSeriesExportModal {...defaultProps} />);

    // Before validation, download is enabled
    const downloadButton = screen.getByTestId("download-button") as HTMLButtonElement;
    expect(downloadButton.disabled).toBe(false);

    // Run validation
    fireEvent.click(screen.getByTestId("validate-button"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-summary")).toBeInTheDocument();
    });

    // After validation with errors, download is disabled
    expect(downloadButton.disabled).toBe(true);
  });

  it("download button enabled when validation passes", async () => {
    const validationResult = {
      valid: true,
      errors: [],
      warnings: [],
      experiment_count: 3,
      sample_count: 15,
    };
    mockApiPost.mockResolvedValueOnce(validationResult);

    render(<SuperSeriesExportModal {...defaultProps} />);

    fireEvent.click(screen.getByTestId("validate-button"));

    await waitFor(() => {
      expect(screen.getByTestId("validation-summary")).toBeInTheDocument();
    });

    const downloadButton = screen.getByTestId("download-button") as HTMLButtonElement;
    expect(downloadButton.disabled).toBe(false);
  });

  it("download triggers API call and file download", async () => {
    const mockBlob = new Blob(["zip content"], { type: "application/zip" });
    const mockResponse = {
      ok: true,
      blob: jest.fn().mockResolvedValue(mockBlob),
      headers: {
        get: jest.fn().mockReturnValue('attachment; filename="geo_superseries.zip"'),
      },
    };

    global.fetch = jest.fn().mockResolvedValueOnce(mockResponse);
    const mockCreateObjectURL = jest.fn().mockReturnValue("blob:mock-url");
    const mockRevokeObjectURL = jest.fn();
    global.URL.createObjectURL = mockCreateObjectURL;
    global.URL.revokeObjectURL = mockRevokeObjectURL;

    const mockClick = jest.fn();
    const mockCreateElement = document.createElement.bind(document);
    jest.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = mockCreateElement(tag);
      if (tag === "a") {
        el.click = mockClick;
      }
      return el;
    });

    render(<SuperSeriesExportModal {...defaultProps} />);

    fireEvent.click(screen.getByTestId("download-button"));

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "http://localhost:8000/api/projects/42/export/geo",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "Content-Type": "application/json",
            Authorization: "Bearer mock-token",
          }),
          body: JSON.stringify({ experiment_ids: [1, 2, 3] }),
        }),
      );
    });

    await waitFor(() => {
      expect(mockClick).toHaveBeenCalled();
    });

    jest.restoreAllMocks();
  });

  it("shows error state on validation failure", async () => {
    mockApiPost.mockRejectedValueOnce(new Error("Network error"));

    render(<SuperSeriesExportModal {...defaultProps} />);

    fireEvent.click(screen.getByTestId("validate-button"));

    await waitFor(() => {
      expect(screen.getByTestId("export-error")).toHaveTextContent("Network error");
    });
  });

  it("calls onClose when close button is clicked", () => {
    render(<SuperSeriesExportModal {...defaultProps} />);

    fireEvent.click(screen.getByTestId("modal-close"));
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when cancel button is clicked", () => {
    render(<SuperSeriesExportModal {...defaultProps} />);

    fireEvent.click(screen.getByText("Cancel"));
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
  });

  it("clears validation when experiment selection changes", async () => {
    const validationResult = {
      valid: true,
      errors: [],
      warnings: [{ level: "warning", message: "Minor issue" }],
      experiment_count: 3,
      sample_count: 15,
    };
    mockApiPost.mockResolvedValueOnce(validationResult);

    render(<SuperSeriesExportModal {...defaultProps} />);

    // Run validation
    fireEvent.click(screen.getByTestId("validate-button"));
    await waitFor(() => {
      expect(screen.getByTestId("validation-summary")).toBeInTheDocument();
    });

    // Toggle an experiment
    fireEvent.click(screen.getByTestId("experiment-checkbox-1"));

    // Validation summary should be cleared
    expect(screen.queryByTestId("validation-summary")).not.toBeInTheDocument();
  });
});
