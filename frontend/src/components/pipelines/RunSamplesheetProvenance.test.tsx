import { render, screen, fireEvent } from "@testing-library/react";
import { RunSamplesheetProvenance } from "./RunSamplesheetProvenance";
import type { RunSamplesheetDesign } from "@/lib/types";

/**
 * Design section 10: a run keeps the exact sheet it was handed AND the design
 * that produced it. Re-deriving the sheet later reads today's samples, today's
 * files and today's mapping, none of which are what the run received, so
 * defending a result means holding the sheet itself.
 *
 * The attribution is not decoration. Whoever fills the design grid is often not
 * whoever launches: the wet-lab scientist knows the grouping, the
 * bioinformatician runs the pipeline. A launcher-only record names the wrong
 * person for the value that turned out wrong, which is exactly what an audited
 * lab needs to know.
 */

const csv = "sample,fastq_1,group\nSAMPLE-101,gs://bucket/a_R1.fastq.gz,gut\nSAMPLE-102,gs://bucket/b_R1.fastq.gz,skin\n";

const design: RunSamplesheetDesign = {
  values: {
    "11": { group: { value: "gut", set_by: "Wet Lab", set_at: "2026-08-16T10:00:00Z" } },
    "12": { group: { value: "skin", set_by: "Wet Lab", set_at: "2026-08-16T10:01:00Z" } },
  },
  bindings: {},
};

const samples = [
  { id: 11, external_id: "SAMPLE-101" },
  { id: 12, external_id: "SAMPLE-102" },
];

it("shows the sheet the run was actually given", () => {
  render(<RunSamplesheetProvenance csv={csv} design={null} samples={samples} />);

  expect(screen.getByRole("columnheader", { name: /group/i })).toBeInTheDocument();
  expect(screen.getByText("SAMPLE-101")).toBeInTheDocument();
  expect(screen.getByText("gut")).toBeInTheDocument();
});

it("offers the raw file, because that is what the run consumed", () => {
  render(<RunSamplesheetProvenance csv={csv} design={null} samples={samples} />);

  const download = screen.getByRole("link", { name: /download/i });
  expect(download).toHaveAttribute("download", "samplesheet.csv");
});

it("names who stated each value and when", () => {
  render(<RunSamplesheetProvenance csv={csv} design={design} samples={samples} />);

  expect(screen.getByRole("columnheader", { name: /stated by/i })).toBeInTheDocument();
  expect(screen.getAllByText("Wet Lab")).toHaveLength(2);
  expect(screen.getAllByText("SAMPLE-101")).toHaveLength(2);
});

it("names the sample rather than its id", () => {
  // The stored design is keyed by sample id, which is the right key and the
  // wrong thing to read.
  render(<RunSamplesheetProvenance csv={csv} design={design} samples={samples} />);

  expect(screen.queryByText(/^11$/)).not.toBeInTheDocument();
});

it("says plainly when a run stated nothing", () => {
  render(<RunSamplesheetProvenance csv={csv} design={{ values: {}, bindings: {} }} samples={samples} />);

  expect(screen.getByText(/No per-sample values were stated for this run/i)).toBeInTheDocument();
});

it("renders nothing for a run with no stored sheet", () => {
  // Runs launched before the snapshot existed have none, and inventing one from
  // today's data would show a sheet the run never saw.
  const { container } = render(<RunSamplesheetProvenance csv={null} design={null} samples={samples} />);

  expect(container).toBeEmptyDOMElement();
});

it("keeps a quoted field whole", () => {
  render(
    <RunSamplesheetProvenance
      csv={'sample,notes\nSAMPLE-101,"gut, proximal"\n'}
      design={null}
      samples={samples}
    />,
  );

  expect(screen.getByText("gut, proximal")).toBeInTheDocument();
});

it("collapses when there is a lot of sheet to scroll past", () => {
  render(<RunSamplesheetProvenance csv={csv} design={null} samples={samples} />);

  fireEvent.click(screen.getByRole("button", { name: /hide samplesheet/i }));

  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
