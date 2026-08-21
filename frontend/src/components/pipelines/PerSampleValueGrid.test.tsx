import { render, screen, fireEvent } from "@testing-library/react";
import { PerSampleValueGrid, type GridSample } from "./PerSampleValueGrid";
import type { PerSampleInputSpec, SamplesheetPrefill } from "@/lib/types";

/**
 * The grid that collects what bioAF may not guess: mag's co-assembly group,
 * rnasplice's contrast. Two properties are the reason it exists in this shape.
 *
 * **Values are keyed by sample id, never by row position.** A paste misaligned
 * by one row assigns every value to the wrong sample and the run completes
 * green with the wrong grouping, which is the exact failure this project
 * refuses to let bioAF commit. It must not let a human commit it silently
 * either, so a paste matches on the identifier whenever the block carries one.
 *
 * **A pipeline's enum constrains a pipeline parameter and never the sample's
 * own record.** XX/XY/NA is sarek's input constraint, not a vocabulary for sex.
 */

const samples: GridSample[] = [
  { id: 11, external_id: "SAMPLE-101", organism: "Homo sapiens", tissue_type: "gut" },
  { id: 12, external_id: "SAMPLE-102", organism: "Homo sapiens", tissue_type: "skin" },
];

const groupSpec: PerSampleInputSpec = {
  name: "group",
  required: true,
  is_file: false,
  sample_field: null,
  allowed_values: [],
  constrained: false,
  description: "Group the sample belongs to for co-assembly",
  format_hint: null,
  required_by: null,
  reason: "missing",
  samples: [],
};

function renderGrid(overrides: Partial<Parameters<typeof PerSampleValueGrid>[0]> = {}) {
  const onChange = jest.fn();
  const utils = render(
    <PerSampleValueGrid
      specs={[groupSpec]}
      samples={samples}
      values={{}}
      onChange={onChange}
      prefill={null}
      {...overrides}
    />,
  );
  return { onChange, ...utils };
}

describe("what the grid asks for", () => {
  it("gives every sample a row and names it", () => {
    renderGrid();

    expect(screen.getByText("SAMPLE-101")).toBeInTheDocument();
    expect(screen.getByText("SAMPLE-102")).toBeInTheDocument();
  });

  it("carries enough context to tell the rows apart", () => {
    // Whoever fills this in is often not whoever selected the samples.
    renderGrid();

    expect(screen.getByText("gut")).toBeInTheDocument();
    expect(screen.getByText("skin")).toBeInTheDocument();
  });

  it("records a typed value against the sample's id", () => {
    const { onChange } = renderGrid();

    fireEvent.change(screen.getByLabelText("group for SAMPLE-101"), { target: { value: "cohort-a" } });

    expect(onChange).toHaveBeenCalledWith({ "11": { group: "cohort-a" } });
  });

  it("explains the column in the pipeline's own words", () => {
    renderGrid();

    expect(screen.getByText(/Group the sample belongs to for co-assembly/)).toBeInTheDocument();
  });
});

describe("a vocabulary is a fence only for a pipeline's own parameter", () => {
  it("offers a closed list for a constrained column", () => {
    renderGrid({
      specs: [{ ...groupSpec, name: "condition", allowed_values: ["treated", "untreated"], constrained: true }],
    });

    const field = screen.getByLabelText("condition for SAMPLE-101");
    expect(field.tagName).toBe("SELECT");
    expect(screen.getAllByRole("option", { name: "treated" }).length).toBeGreaterThan(0);
  });

  it("leaves a field recorded on the sample open, whatever the pipeline accepts", () => {
    // raredisease's sex is a PED code. XXY, X0, XYY and mosaics are all real, so
    // the pipeline's list travels as information and never as a fence.
    renderGrid({
      specs: [
        {
          ...groupSpec,
          name: "sex",
          sample_field: "sex",
          allowed_values: ["0", "1", "2"],
          constrained: false,
        },
      ],
    });

    const field = screen.getByLabelText("sex for SAMPLE-101");
    expect(field.tagName).toBe("INPUT");
    expect(screen.getByText(/This pipeline accepts: 0, 1, 2/)).toBeInTheDocument();
  });
});

describe("filling many rows at once", () => {
  it("fills a column down from the first answer", () => {
    const { onChange } = renderGrid({ values: { "11": { group: "cohort-a" } } });

    fireEvent.click(screen.getByRole("button", { name: "Fill group down" }));

    expect(onChange).toHaveBeenCalledWith({
      "11": { group: "cohort-a" },
      "12": { group: "cohort-a" },
    });
  });

  it("matches a pasted block on the identifier, not the row order", () => {
    // The spreadsheet is sorted differently from the grid. Position would put
    // 102's group on 101 and still run.
    const { onChange } = renderGrid();

    fireEvent.paste(screen.getByLabelText("Paste sample values"), {
      clipboardData: { getData: () => "sample\tgroup\nSAMPLE-102\tskin-cohort\nSAMPLE-101\tgut-cohort\n" },
    });

    expect(onChange).toHaveBeenCalledWith({
      "12": { group: "skin-cohort" },
      "11": { group: "gut-cohort" },
    });
  });

  it("reports identifiers in the paste that name no selected sample", () => {
    renderGrid();

    fireEvent.paste(screen.getByLabelText("Paste sample values"), {
      clipboardData: { getData: () => "sample\tgroup\nSAMPLE-101\tgut\nSAMPLE-999\tmystery\n" },
    });

    expect(screen.getByText(/SAMPLE-999/)).toBeInTheDocument();
  });

  it("reports samples the paste did not name", () => {
    renderGrid();

    fireEvent.paste(screen.getByLabelText("Paste sample values"), {
      clipboardData: { getData: () => "sample\tgroup\nSAMPLE-101\tgut\n" },
    });

    expect(screen.getByRole("status")).toHaveTextContent(/Not named by this paste: SAMPLE-102/);
  });

  it("refuses to guess when a pasted block carries no identifier", () => {
    const { onChange } = renderGrid();

    fireEvent.paste(screen.getByLabelText("Paste sample values"), {
      clipboardData: { getData: () => "gut-cohort\nskin-cohort\n" },
    });

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/no sample identifier/i)).toBeInTheDocument();
  });

  it("pastes by row order only when the user says so", () => {
    const { onChange } = renderGrid();

    fireEvent.paste(screen.getByLabelText("Paste sample values"), {
      clipboardData: { getData: () => "gut-cohort\nskin-cohort\n" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply in row order" }));

    expect(onChange).toHaveBeenCalledWith({
      "11": { group: "gut-cohort" },
      "12": { group: "skin-cohort" },
    });
  });
});

describe("a design carried over from an earlier run", () => {
  const prefill: SamplesheetPrefill = {
    scope: "experiment",
    values: { "11": { group: "gut-cohort" } },
    bindings: {},
    samples_without_values: [12],
  };

  it("says where the carried-over design came from", () => {
    renderGrid({ prefill });

    expect(screen.getByText(/carried over from this experiment/i)).toBeInTheDocument();
  });

  it("names the samples the design does not cover", () => {
    // A grouping that was right for six samples may be wrong for twelve, and a
    // prefilled value looks plausible precisely because it was right last time.
    renderGrid({ prefill });

    expect(screen.getByText(/1 sample has been added since this design was set/i)).toBeInTheDocument();
    expect(screen.getByText(/Review group before launching/i)).toBeInTheDocument();
  });

  it("offers the carried-over values rather than applying them", () => {
    const { onChange } = renderGrid({ prefill });

    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Use carried-over values" }));

    expect(onChange).toHaveBeenCalledWith({ "11": { group: "gut-cohort" } });
  });
});
