import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FileTreeSelector } from "./FileTreeSelector";
import type { FileResponse } from "@/lib/types";

function makeFile(overrides: Partial<FileResponse> & { id: number; filename: string }): FileResponse {
  return {
    id: overrides.id,
    filename: overrides.filename,
    gcs_uri: overrides.gcs_uri ?? `gs://bucket/experiments/1/uploads/${overrides.filename}`,
    size_bytes: overrides.size_bytes ?? 1024,
    md5_checksum: null,
    file_type: overrides.file_type ?? "other",
    tags: [],
    uploader: null,
    project_id: 1,
    experiment_id: 1,
    sample_ids: overrides.sample_ids ?? [10],
    source_type: overrides.source_type ?? "upload",
    source_pipeline_run_id: null,
    source_notebook_session_id: null,
    storage_deleted: false,
    upload_timestamp: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  };
}

const sampleNames = { 10: "Sample A" };

const tenX_trio_and_h5ad = [
  makeFile({ id: 1, filename: "matrix.mtx.gz" }),
  makeFile({ id: 2, filename: "features.tsv.gz" }),
  makeFile({ id: 3, filename: "barcodes.tsv.gz" }),
  makeFile({ id: 4, filename: "result.h5ad" }),
];

const noisy_files = [
  ...tenX_trio_and_h5ad,
  makeFile({ id: 5, filename: "reads_R1.fastq.gz" }),
  makeFile({ id: 6, filename: "aligned.bam" }),
  makeFile({ id: 7, filename: "qc_report.html" }),
  makeFile({ id: 8, filename: "metadata.csv" }),
  makeFile({ id: 9, filename: "raw.h5" }),
  makeFile({ id: 10, filename: "notes.txt" }),
];

describe("FileTreeSelector filter bar", () => {
  it("renders filter chips for Defaults, H5, CSV/TSV, Reports, FASTQ, BAM, Other with counts", () => {
    render(
      <FileTreeSelector
        files={noisy_files}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );
    const bar = screen.getByRole("group", { name: /file type filters/i });
    expect(within(bar).getByRole("button", { name: /^Defaults \(4\)$/i })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: /^H5 \(1\)$/i })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: /^CSV\/TSV \(1\)$/i })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: /^Reports \(1\)$/i })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: /^FASTQ \(1\)$/i })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: /^BAM \(1\)$/i })).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: /^Other \(1\)$/i })).toBeInTheDocument();
  });

  it("only Defaults is active on mount; only matching files visible", () => {
    render(
      <FileTreeSelector
        files={noisy_files}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );
    expect(screen.getByText("matrix.mtx.gz")).toBeInTheDocument();
    expect(screen.getByText("features.tsv.gz")).toBeInTheDocument();
    expect(screen.getByText("barcodes.tsv.gz")).toBeInTheDocument();
    expect(screen.getByText("result.h5ad")).toBeInTheDocument();
    expect(screen.queryByText("reads_R1.fastq.gz")).not.toBeInTheDocument();
    expect(screen.queryByText("aligned.bam")).not.toBeInTheDocument();
    expect(screen.queryByText("qc_report.html")).not.toBeInTheDocument();
    expect(screen.queryByText("metadata.csv")).not.toBeInTheDocument();
    expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
    expect(screen.queryByText("raw.h5")).not.toBeInTheDocument();
  });

  it("pre-selects all Defaults-matching files on mount", () => {
    const onSelectionChange = jest.fn();
    render(
      <FileTreeSelector
        files={noisy_files}
        sampleNames={sampleNames}
        onSelectionChange={onSelectionChange}
      />
    );
    const last = onSelectionChange.mock.calls.at(-1)?.[0] as number[] | undefined;
    expect(last).toBeDefined();
    expect(new Set(last)).toEqual(new Set([1, 2, 3, 4]));
  });

  it("toggling FASTQ on adds FASTQ files without pre-selecting them", async () => {
    const user = userEvent.setup();
    const onSelectionChange = jest.fn();
    render(
      <FileTreeSelector
        files={noisy_files}
        sampleNames={sampleNames}
        onSelectionChange={onSelectionChange}
      />
    );
    await user.click(screen.getByRole("button", { name: /^FASTQ \(1\)$/i }));
    expect(screen.getByText("reads_R1.fastq.gz")).toBeInTheDocument();
    const last = onSelectionChange.mock.calls.at(-1)?.[0] as number[];
    expect(new Set(last)).toEqual(new Set([1, 2, 3, 4]));
  });

  it("toggling Defaults off hides those files but preserves checked state in selection", async () => {
    const user = userEvent.setup();
    const onSelectionChange = jest.fn();
    render(
      <FileTreeSelector
        files={noisy_files}
        sampleNames={sampleNames}
        onSelectionChange={onSelectionChange}
      />
    );
    await user.click(screen.getByRole("button", { name: /^Defaults \(4\)$/i }));
    expect(screen.queryByText("matrix.mtx.gz")).not.toBeInTheDocument();
    const last = onSelectionChange.mock.calls.at(-1)?.[0] as number[];
    expect(new Set(last)).toEqual(new Set([1, 2, 3, 4]));
  });

  it("unchecking a pre-checked default file and toggling filters does not re-check it", async () => {
    const user = userEvent.setup();
    const onSelectionChange = jest.fn();
    render(
      <FileTreeSelector
        files={noisy_files}
        sampleNames={sampleNames}
        onSelectionChange={onSelectionChange}
      />
    );
    const matrixCheckbox = screen.getByRole("checkbox", { name: "matrix.mtx.gz" });
    await user.click(matrixCheckbox);
    let last = onSelectionChange.mock.calls.at(-1)?.[0] as number[];
    expect(new Set(last)).toEqual(new Set([2, 3, 4]));
    const fastqChip = screen.getByRole("button", { name: /^FASTQ \(1\)$/i });
    await user.click(fastqChip);
    await user.click(fastqChip);
    last = onSelectionChange.mock.calls.at(-1)?.[0] as number[];
    expect(new Set(last)).toEqual(new Set([2, 3, 4]));
  });

  it("renders empty-state message when all filter chips are off", async () => {
    const user = userEvent.setup();
    render(
      <FileTreeSelector
        files={noisy_files}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );
    await user.click(screen.getByRole("button", { name: /^Defaults \(4\)$/i }));
    expect(screen.getByText(/no files match/i)).toBeInTheDocument();
  });

  it("does not render the legacy 'Include FASTQ and BAM files' checkbox", () => {
    render(
      <FileTreeSelector
        files={noisy_files}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );
    expect(
      screen.queryByLabelText(/include fastq and bam files/i)
    ).not.toBeInTheDocument();
  });

  it("Other filter catches files with unknown extensions", async () => {
    const user = userEvent.setup();
    render(
      <FileTreeSelector
        files={noisy_files}
        sampleNames={sampleNames}
        onSelectionChange={() => {}}
      />
    );
    await user.click(screen.getByRole("button", { name: /^Other \(1\)$/i }));
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
  });

  it("filename matching is case-insensitive (MATRIX.MTX.GZ counts as Defaults)", () => {
    const upper = [makeFile({ id: 99, filename: "MATRIX.MTX.GZ" })];
    const onSelectionChange = jest.fn();
    render(
      <FileTreeSelector
        files={upper}
        sampleNames={sampleNames}
        onSelectionChange={onSelectionChange}
      />
    );
    expect(screen.getByText("MATRIX.MTX.GZ")).toBeInTheDocument();
    const last = onSelectionChange.mock.calls.at(-1)?.[0] as number[];
    expect(new Set(last)).toEqual(new Set([99]));
  });
});
