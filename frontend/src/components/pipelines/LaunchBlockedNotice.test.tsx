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

it("says a row is incomplete rather than that the field cannot be derived", () => {
  render(<LaunchBlockedNotice preflight={incompleteRow} />);

  // The remedy is a FILE for one row, not a value for the sample, so the
  // fallback wording must not run here.
  expect(screen.queryByText(/not something bioAF can derive/i)).not.toBeInTheDocument();
  expect(screen.getByText(/would be empty/i)).toBeInTheDocument();
  expect(screen.getByText(/SAMPLE-101/)).toBeInTheDocument();
});
