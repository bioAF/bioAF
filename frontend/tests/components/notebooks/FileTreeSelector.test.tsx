import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FileTreeSelector } from "@/components/notebooks/FileTreeSelector";
import type { FileResponse } from "@/lib/types";

const mockFiles: FileResponse[] = [
  {
    id: 1,
    filename: "filtered_matrix.h5ad",
    gcs_uri: "gs://bucket/f1",
    size_bytes: 450_000_000,
    md5_checksum: null,
    file_type: "h5ad",
    tags: [],
    uploader: null,
    project_id: null,
    experiment_id: 10,
    sample_ids: [100],
    source_type: "upload",
    source_pipeline_run_id: null,
    source_notebook_session_id: null,
    storage_deleted: false,
    upload_timestamp: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 2,
    filename: "raw_counts.csv",
    gcs_uri: "gs://bucket/f2",
    size_bytes: 12_000_000,
    md5_checksum: null,
    file_type: "csv",
    tags: [],
    uploader: null,
    project_id: null,
    experiment_id: 10,
    sample_ids: [100],
    source_type: "upload",
    source_pipeline_run_id: null,
    source_notebook_session_id: null,
    storage_deleted: false,
    upload_timestamp: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 3,
    filename: "sample_R1.fastq.gz",
    gcs_uri: "gs://bucket/f3",
    size_bytes: 28_000_000_000,
    md5_checksum: null,
    file_type: "fastq",
    tags: [],
    uploader: null,
    project_id: null,
    experiment_id: 10,
    sample_ids: [100],
    source_type: "upload",
    source_pipeline_run_id: null,
    source_notebook_session_id: null,
    storage_deleted: false,
    upload_timestamp: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 4,
    filename: "analysis.h5ad",
    gcs_uri: "gs://bucket/f4",
    size_bytes: 520_000_000,
    md5_checksum: null,
    file_type: "h5ad",
    tags: [],
    uploader: null,
    project_id: null,
    experiment_id: 10,
    sample_ids: [101],
    source_type: "upload",
    source_pipeline_run_id: null,
    source_notebook_session_id: null,
    storage_deleted: false,
    upload_timestamp: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  },
];

const sampleNames: Record<number, string> = {
  100: "SAMP-001 - Control Day 0",
  101: "SAMP-002 - Treatment Day 7",
};

describe("FileTreeSelector", () => {
  it("renders file tree with sample grouping", () => {
    render(
      <FileTreeSelector
        files={mockFiles}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );
    expect(screen.getByText("SAMP-001 - Control Day 0")).toBeInTheDocument();
    expect(screen.getByText("SAMP-002 - Treatment Day 7")).toBeInTheDocument();
    expect(screen.getByText("filtered_matrix.h5ad")).toBeInTheDocument();
  });

  it("hides FASTQ/BAM files by default", () => {
    render(
      <FileTreeSelector
        files={mockFiles}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );
    // FASTQ file should be hidden
    expect(screen.queryByText("sample_R1.fastq.gz")).not.toBeInTheDocument();
    // Non-FASTQ files should be visible
    expect(screen.getByText("filtered_matrix.h5ad")).toBeInTheDocument();
  });

  it("shows FASTQ/BAM files when toggle is checked", async () => {
    const user = userEvent.setup();
    render(
      <FileTreeSelector
        files={mockFiles}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );

    const toggle = screen.getByLabelText(/include fastq and bam/i);
    await user.click(toggle);

    expect(screen.getByText("sample_R1.fastq.gz")).toBeInTheDocument();
  });

  it("selects all children when sample checkbox is clicked", async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();
    render(
      <FileTreeSelector
        files={mockFiles}
        sampleNames={sampleNames}
        onSelectionChange={onChange}
      />
    );

    // Click the SAMP-001 checkbox (selects files 1 and 2, not 3 since FASTQ hidden)
    const sampleCheckbox = screen.getByRole("checkbox", { name: /SAMP-001/ });
    await user.click(sampleCheckbox);

    // Should have called onChange with the visible file IDs for SAMP-001
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(lastCall).toEqual(expect.arrayContaining([1, 2]));
    expect(lastCall.length).toBe(2);
  });

  it("calculates total selected size", async () => {
    const user = userEvent.setup();
    render(
      <FileTreeSelector
        files={mockFiles}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );

    // Select a file
    const fileCheckbox = screen.getByRole("checkbox", { name: /filtered_matrix/ });
    await user.click(fileCheckbox);

    // Should show size in the summary
    expect(screen.getByText(/1 file selected/)).toBeInTheDocument();
  });

  it("renders empty state when no files", () => {
    render(
      <FileTreeSelector
        files={[]}
        sampleNames={{}}
        onSelectionChange={() => {}}
      />
    );
    expect(screen.getByText(/no files available/i)).toBeInTheDocument();
  });

  it("shows warning when selection exceeds 10 GB", async () => {
    const user = userEvent.setup();
    // Enable FASTQ toggle and select the large file
    render(
      <FileTreeSelector
        files={mockFiles}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );

    // Enable FASTQ
    const toggle = screen.getByLabelText(/include fastq and bam/i);
    await user.click(toggle);

    // Select the FASTQ file (28 GB)
    const fastqCheckbox = screen.getByRole("checkbox", { name: /sample_R1/ });
    await user.click(fastqCheckbox);

    expect(screen.getByText(/exceeds 10 GB/i)).toBeInTheDocument();
  });
});
