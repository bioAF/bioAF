import { render, screen, fireEvent } from "@testing-library/react";
import { SamplesheetReview } from "./SamplesheetReview";
import type { SamplesheetPreview } from "@/lib/types";

/**
 * Design section 6: every launch ends with a human-readable review of the sheet
 * that is about to run. Not because bioAF distrusts its own generation, but
 * because a regex match is not proof of the right file: a reference genome
 * satisfies funcscan's `fasta` pattern exactly as well as the scientist's
 * assembly does. This table is the only thing standing between that and a
 * confidently wrong result.
 *
 * Section 7: a wrong value is corrected HERE, in place, and the correction
 * applies to this run. Promoting it into the saved design is a separate,
 * explicit act, so a hurried fix never silently changes what the next launch
 * inherits.
 */

const preview: SamplesheetPreview = {
  columns: ["sample", "fastq_1", "group"],
  rows: [
    {
      sample_id: 11,
      external_id: "SAMPLE-101",
      values: ["SAMPLE-101", "gs://bucket/SAMPLE-101_R1.fastq.gz", "gut"],
    },
    {
      sample_id: 12,
      external_id: "SAMPLE-102",
      values: ["SAMPLE-102", "gs://bucket/SAMPLE-102_R1.fastq.gz", "skin"],
    },
  ],
  csv: "sample,fastq_1,group\nSAMPLE-101,gs://bucket/SAMPLE-101_R1.fastq.gz,gut\n",
  omissions: [],
};

function renderReview(overrides: Partial<Parameters<typeof SamplesheetReview>[0]> = {}) {
  const onCorrect = jest.fn();
  const utils = render(<SamplesheetReview preview={preview} onCorrect={onCorrect} {...overrides} />);
  return { onCorrect, ...utils };
}

describe("the sheet the run would submit", () => {
  it("renders as a table, not as raw text", () => {
    renderReview();

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /group/i })).toBeInTheDocument();
    expect(screen.getByLabelText("group for SAMPLE-101")).toHaveValue("gut");
  });

  it("keeps the raw file behind a button for whoever wants it", () => {
    renderReview();

    expect(screen.queryByText(/sample,fastq_1,group/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show raw csv/i }));

    expect(screen.getByText(/sample,fastq_1,group/)).toBeInTheDocument();
  });

  it("says nothing about a sheet it was not given", () => {
    const { container } = render(<SamplesheetReview preview={null} onCorrect={jest.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("correcting a wrong value in place", () => {
  it("reports the correction against the sample and column, not the position", () => {
    const { onCorrect } = renderReview();

    fireEvent.change(screen.getByLabelText("group for SAMPLE-101"), { target: { value: "cohort-b" } });

    expect(onCorrect).toHaveBeenCalledWith(11, "group", "cohort-b");
  });

  it("says a correction applies to this run alone", () => {
    renderReview();

    expect(screen.getByText(/applies to this run/i)).toBeInTheDocument();
  });

  it("does not offer to edit a row it could not attribute to a sample", () => {
    // A tailored generator can emit a row bioAF cannot match back to a sample
    // (chipseq pairs an IP sample with a detected control). Editing it would
    // have to guess which sample the correction belongs to.
    renderReview({
      preview: {
        ...preview,
        rows: [{ sample_id: null, external_id: null, values: ["CONTROL", "gs://bucket/ctrl_R1.fastq.gz", ""] }],
      },
    });

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText("CONTROL")).toBeInTheDocument();
  });
});

describe("what the sheet leaves out", () => {
  it("names the value the pipeline could not express, and what it accepts", () => {
    renderReview({
      preview: {
        ...preview,
        omissions: [
          {
            column: "sex",
            sample_id: 11,
            external_id: "SAMPLE-101",
            value: "47,XXY",
            reason: "not_in_enum",
            allowed_values: ["XX", "XY", "NA"],
          },
        ],
      },
    });

    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("sex");
    expect(note).toHaveTextContent("47,XXY");
    expect(note).toHaveTextContent("SAMPLE-101");
    expect(note).toHaveTextContent("XX, XY, NA");
  });

  it("stays quiet when the sheet left nothing out", () => {
    renderReview();

    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});
