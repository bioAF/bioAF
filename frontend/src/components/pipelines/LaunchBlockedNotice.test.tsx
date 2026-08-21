import { render, screen } from "@testing-library/react";
import { LaunchBlockedNotice } from "./LaunchBlockedNotice";
import type { PipelineRunPreflight } from "@/lib/types";

/**
 * Five of the twenty most popular pipelines failed only after the user clicked
 * through every step and pressed Launch. The decision was right; the timing was
 * not. This renders the same answer while it is still actionable.
 */

const notLaunchable: PipelineRunPreflight = {
  can_launch: false,
  code: "pipeline_not_sample_launchable",
  reason: "This pipeline does not run on sequencing reads, so it cannot be launched from samples. It expects 'fasta' instead.",
  details: { required_inputs: ["fasta"] },
};

const missingFields: PipelineRunPreflight = {
  can_launch: false,
  code: "samples_missing_required_fields",
  reason: "This pipeline requires 'patient', which is missing for some samples.",
  details: {
    missing_columns: {
      patient: {
        sample_field: "donor_source",
        allowed_values: [],
        samples: [
          { id: 2, external_id: "SAMPLE-102" },
          { id: 3, external_id: "SAMPLE-103" },
        ],
      },
    },
  },
};

/** A sample sequenced over two lanes emits two rows, and one of them lost a
 *  mate. The sample HAS reads, so "bioAF cannot derive this" would send the
 *  scientist to fill in a field when what is missing is a file. */
const incompleteRow: PipelineRunPreflight = {
  can_launch: false,
  code: "samples_missing_required_fields",
  reason: "This pipeline requires 'fastq_1', which is missing for some samples.",
  details: {
    missing_columns: {
      fastq_1: {
        sample_field: null,
        allowed_values: [],
        reason: "empty_in_row",
        samples: [{ id: 7, external_id: "SAMPLE-101" }],
      },
    },
  },
};

it("renders nothing when the launch is fine", () => {
  const { container } = render(
    <LaunchBlockedNotice preflight={{ can_launch: true, code: null, reason: null, details: {} }} />,
  );
  expect(container).toBeEmptyDOMElement();
});

it("renders nothing before the check has run", () => {
  const { container } = render(<LaunchBlockedNotice preflight={null} />);
  expect(container).toBeEmptyDOMElement();
});

it("says what a non-read pipeline wants instead", () => {
  render(<LaunchBlockedNotice preflight={notLaunchable} />);

  expect(screen.getByText(/does not run on sequencing reads/i)).toBeInTheDocument();
  expect(screen.getByText(/fasta/)).toBeInTheDocument();
});

it("names the missing column and the field that would supply it", () => {
  render(<LaunchBlockedNotice preflight={missingFields} />);

  // "patient" appears in the reason sentence too; the list entry is the one
  // that has to be there, so assert on the emphasised column name.
  expect(screen.getAllByText(/patient/).length).toBeGreaterThan(0);
  expect(screen.getByText("donor source")).toBeInTheDocument();
});

it("lists only the samples actually missing the value", () => {
  render(<LaunchBlockedNotice preflight={missingFields} />);

  expect(screen.getByText(/SAMPLE-102/)).toBeInTheDocument();
  expect(screen.getByText(/SAMPLE-103/)).toBeInTheDocument();
});

it("says which column made an otherwise optional one required", () => {
  // mag's schema lists only sample and group as required, then adds
  // "a row with short_reads_1 must also carry short_reads_platform". Reporting
  // the platform as simply missing sends the user looking for a rule that is
  // not in the required list.
  const dependent: PipelineRunPreflight = {
    can_launch: false,
    code: "samples_missing_required_fields",
    reason: "This pipeline requires 'short_reads_platform', which is missing for some samples.",
    details: {
      missing_columns: {
        short_reads_platform: {
          sample_field: null,
          allowed_values: ["ILLUMINA", "BGISEQ"],
          required_by: "short_reads_1",
          samples: [{ id: 1, external_id: "GUT_A" }],
        },
      },
    },
  };
  render(<LaunchBlockedNotice preflight={dependent} />);

  expect(screen.getByText(/is required because these samples carry/i)).toBeInTheDocument();
  expect(screen.getAllByText("short reads 1").length).toBeGreaterThan(0);
  expect(screen.getByText(/GUT_A/)).toBeInTheDocument();
});

it("names the value a pipeline rejected and suggests one it would take", () => {
  // SAMPLE-101 is a real name on the demo and ampliseq rejects the hyphen.
  // bioAF does not rename the sample: it says what the problem is and offers a
  // spelling that works, and the scientist decides.
  const badCharacters: PipelineRunPreflight = {
    can_launch: false,
    code: "samples_missing_required_fields",
    reason: "This pipeline requires 'sample', which is missing for some samples.",
    details: {
      missing_columns: {
        sample: {
          sample_field: "external_id",
          allowed_values: [],
          reason: "invalid_characters",
          pattern: "^[a-zA-Z][a-zA-Z0-9_]+$",
          samples: [{ id: 1, external_id: "SAMPLE-101", value: "SAMPLE-101", suggestion: "SAMPLE_101" }],
        },
      },
    },
  };
  render(<LaunchBlockedNotice preflight={badCharacters} />);

  expect(screen.getByText(/will not accept/i)).toBeInTheDocument();
  expect(screen.getByText(/SAMPLE-101/)).toBeInTheDocument();
  expect(screen.getByText(/SAMPLE_101/)).toBeInTheDocument();
});

it("says so plainly when no spelling would work", () => {
  // A mistyped accession cannot be repaired by punctuation, so offering nothing
  // is the honest answer and the copy must not imply a fix is available.
  const noFix: PipelineRunPreflight = {
    can_launch: false,
    code: "samples_missing_required_fields",
    reason: "This pipeline requires 'ncbi', which is missing for some samples.",
    details: {
      missing_columns: {
        ncbi: {
          sample_field: null,
          allowed_values: [],
          reason: "invalid_characters",
          pattern: "^GC[AF]_[0-9]{9}\\.[0-9]+$",
          samples: [{ id: 1, external_id: "S1", value: "not an accession", suggestion: null }],
        },
      },
    },
  };
  render(<LaunchBlockedNotice preflight={noFix} />);

  expect(screen.getByText(/not an accession/)).toBeInTheDocument();
  expect(screen.queryByText(/suggested/i)).not.toBeInTheDocument();
});

it("warns when two samples would end up with the same name", () => {
  const collision: PipelineRunPreflight = {
    can_launch: false,
    code: "samples_missing_required_fields",
    reason: "This pipeline requires 'sample', which is missing for some samples.",
    details: {
      missing_columns: {
        sample: {
          sample_field: "external_id",
          allowed_values: [],
          reason: "collision",
          pattern: "^[a-zA-Z][a-zA-Z0-9_]+$",
          samples: [
            { id: 1, external_id: "SAMPLE-1", suggestion: null },
            { id: 2, external_id: "SAMPLE_1", suggestion: null },
          ],
        },
      },
    },
  };
  render(<LaunchBlockedNotice preflight={collision} />);

  expect(screen.getByText(/same name/i)).toBeInTheDocument();
  expect(screen.getByText(/SAMPLE-1, SAMPLE_1/)).toBeInTheDocument();
});

it("shows the allowed values when the column is constrained", () => {
  const enumBlocked: PipelineRunPreflight = {
    can_launch: false,
    code: "samples_missing_required_fields",
    reason: "This pipeline requires 'instrument_platform', which is missing for some samples.",
    details: {
      missing_columns: {
        instrument_platform: {
          sample_field: null,
          allowed_values: ["ILLUMINA", "OXFORD_NANOPORE"],
          samples: [{ id: 1, external_id: "SAMPLE-101" }],
        },
      },
    },
  };
  render(<LaunchBlockedNotice preflight={enumBlocked} />);

  expect(screen.getByText(/ILLUMINA/)).toBeInTheDocument();
});

/**
 * A column the pipeline uses to tell two rows apart, which would repeat. Neither
 * "missing" nor "will not accept" is true of it: the value is absent for every
 * sample, and supplying the same value everywhere would not help. mag declares
 * `run: {unique: ["sample"]}` and a two-lane sample produces two rows it cannot
 * distinguish.
 */
const notUnique: PipelineRunPreflight = {
  can_launch: false,
  code: "samples_missing_required_fields",
  reason: "This pipeline needs 'run' to tell some rows apart, and they would repeat.",
  details: {
    missing_columns: {
      run: {
        sample_field: null,
        allowed_values: [],
        reason: "not_unique",
        unique_with: ["sample"],
        samples: [{ id: 5, external_id: "GUT_A" }],
      },
    },
  },
};

it("says the rows would repeat rather than that the column is missing", () => {
  render(<LaunchBlockedNotice preflight={notUnique} />);

  expect(screen.getByRole("alert")).toHaveTextContent(/more than one row/i);
  expect(screen.queryByText(/is not something bioAF can derive/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Missing for:/i)).not.toBeInTheDocument();
});

it("names the column the pipeline pairs it with, and the samples that repeat", () => {
  render(<LaunchBlockedNotice preflight={notUnique} />);

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("run");
  expect(alert).toHaveTextContent("sample");
  expect(alert).toHaveTextContent("GUT_A");
});

/**
 * The same repetition, where bioAF DOES know the rows came off one sequencing
 * run. Two lanes of one flow cell are the same run, so no value of `run` could
 * separate them: any that did would be a lane wearing a run's name. Offering the
 * field here is a wrong answer wearing the shape of a fix.
 */
const cameOffOneRun: PipelineRunPreflight = {
  can_launch: false,
  code: "samples_missing_required_fields",
  reason:
    "Some rows came off one sequencing run, so no value of 'run' could tell them apart. " +
    "Merge those reads, or choose a pipeline that reads a lane.",
  details: {
    missing_columns: {
      run: {
        sample_field: null,
        allowed_values: [],
        reason: "not_unique",
        unique_with: ["sample"],
        remedy: "merge_reads",
        repeated: [{ run: "HLK3VDSX7", source: "flowcell", lanes: ["1", "2"] }],
        samples: [{ id: 5, external_id: "GUT_A" }],
      },
    },
  },
};

/** ampliseq. Its rule is on the sample's own name ALONE, so the only field on
 *  offer is the one that must not change. */
const oneRowPerSample: PipelineRunPreflight = {
  can_launch: false,
  code: "samples_missing_required_fields",
  reason:
    "This pipeline takes one row per sample, and some samples have more than one set of reads. " +
    "Merge those reads, or launch them as separate samples.",
  details: {
    missing_columns: {
      sample: {
        sample_field: "external_id",
        allowed_values: [],
        reason: "not_unique",
        unique_with: [],
        remedy: "one_row_per_sample",
        repeated: [],
        samples: [{ id: 5, external_id: "SAMPLEA" }],
      },
    },
  },
};

it("names the flow cell and the lanes that came off one sequencing run", () => {
  render(<LaunchBlockedNotice preflight={cameOffOneRun} />);

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(/one sequencing run/i);
  expect(alert).toHaveTextContent("HLK3VDSX7");
  expect(alert).toHaveTextContent(/lanes 1 and 2/i);
});

it("offers the remedy rather than a value, where no value would separate the rows", () => {
  render(<LaunchBlockedNotice preflight={cameOffOneRun} />);

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(/merge those reads/i);
  expect(alert).toHaveTextContent(/pipeline that reads a lane/i);
  // The wording this item removes. It sends the scientist to supply a value for
  // a column bioAF filled itself, and nothing they type unblocks the launch.
  expect(alert).not.toHaveTextContent(/has to differ between rows/i);
});

it("does not lead the one-row-per-sample block with the sample's own name", () => {
  render(<LaunchBlockedNotice preflight={oneRowPerSample} />);

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(/takes one row per sample/i);
  expect(alert).toHaveTextContent("SAMPLEA");
  // Naming the column first is what sent scientists to rename their sample,
  // which corrupts the LIMS record and still does not launch.
  expect(alert).not.toHaveTextContent(/has to be different in every row/i);
});

it("says a row is incomplete rather than that the field cannot be derived", () => {
  render(<LaunchBlockedNotice preflight={incompleteRow} />);

  // The remedy is a FILE for one row, not a value for the sample, so the
  // fallback wording must not run here.
  expect(screen.queryByText(/not something bioAF can derive/i)).not.toBeInTheDocument();
  expect(screen.getByText(/would be empty/i)).toBeInTheDocument();
  expect(screen.getByText(/SAMPLE-101/)).toBeInTheDocument();
});

/**
 * The block is a summary sentence plus a per-column detail, written for the
 * same fact by different hands. Every test above asks whether some wording is
 * PRESENT, which is why all sixteen passed while the alert said this on the
 * deployed demo:
 *
 *     This pipeline takes one row per sample, and some samples have more than
 *     one set of reads. Merge those reads, or launch them as separate samples.
 *
 *     This pipeline takes one row per sample, and these have more than one set
 *     of reads: SAMPLE-101
 *     Merge those reads, or launch them as separate samples.
 *
 * A block that repeats itself reads as a rendering mistake, at exactly the
 * point the scientist is deciding whether to trust what bioAF is telling them
 * about their data. So these count rather than look.
 */

/** The whole alert as one whitespace-normalised string, which is how a person
 *  reads it: the summary and the detail are one paragraph to them, whatever
 *  elements they arrive in. */
function alertText(): string {
  return (screen.getByRole("alert").textContent ?? "").replace(/\s+/g, " ").trim();
}

function timesSaid(phrase: string): number {
  return alertText().split(phrase).length - 1;
}

it("states the one-row-per-sample rule and its remedy once each", () => {
  render(<LaunchBlockedNotice preflight={oneRowPerSample} />);

  expect(timesSaid("takes one row per sample")).toBe(1);
  expect(timesSaid("Merge those reads, or launch them as separate samples")).toBe(1);
  // What the summary cannot say, and the only reason the detail exists.
  expect(screen.getByRole("alert")).toHaveTextContent("SAMPLEA");
});

it("states the merge-reads rule and its remedy once each", () => {
  render(<LaunchBlockedNotice preflight={cameOffOneRun} />);

  expect(timesSaid("came off one sequencing run")).toBe(1);
  expect(timesSaid("choose a pipeline that reads a lane")).toBe(1);
  // The flow cell and the lanes are what the summary cannot name.
  expect(screen.getByRole("alert")).toHaveTextContent("HLK3VDSX7");
  expect(screen.getByRole("alert")).toHaveTextContent(/lanes 1 and 2/i);
});

/** Two columns whose remedies DIFFER. The summary can then only say that no
 *  value would separate the rows, so it names neither remedy, and dropping them
 *  from the detail would leave the scientist with nothing to do. This is the
 *  case a blanket trim would break, which is why the trim is conditional. */
const twoDifferentRemedies: PipelineRunPreflight = {
  can_launch: false,
  code: "samples_missing_required_fields",
  reason: "Some rows cannot be told apart by 'run' and 'sample', and no value would separate them.",
  details: {
    missing_columns: {
      run: {
        sample_field: null,
        allowed_values: [],
        reason: "not_unique",
        unique_with: ["sample"],
        remedy: "merge_reads",
        repeated: [{ run: "HLK3VDSX7", source: "flowcell", lanes: ["1", "2"] }],
        samples: [{ id: 5, external_id: "GUT_A" }],
      },
      sample: {
        sample_field: "external_id",
        allowed_values: [],
        reason: "not_unique",
        unique_with: [],
        remedy: "one_row_per_sample",
        repeated: [],
        samples: [{ id: 5, external_id: "GUT_A" }],
      },
    },
  },
};

it("keeps both remedies in the detail when the summary names neither", () => {
  render(<LaunchBlockedNotice preflight={twoDifferentRemedies} />);

  expect(timesSaid("choose a pipeline that reads a lane")).toBe(1);
  expect(timesSaid("launch them as separate samples")).toBe(1);
});
