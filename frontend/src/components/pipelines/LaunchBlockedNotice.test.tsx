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
