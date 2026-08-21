import { render, screen, fireEvent } from "@testing-library/react";
import { SamplesheetInputs } from "./SamplesheetInputs";
import type { SamplesheetInputSpec } from "@/lib/types";

const platform: SamplesheetInputSpec = {
  name: "instrument_platform",
  parameter: "instrument_platform",
  required: true,
  allowed_values: ["ILLUMINA", "OXFORD_NANOPORE", "PACBIO_SMRT"],
};

const readStructure: SamplesheetInputSpec = {
  name: "read_structure",
  parameter: "read_structure",
  required: true,
  allowed_values: [],
};

it("renders nothing when the pipeline needs no extra samplesheet values", () => {
  const { container } = render(
    <SamplesheetInputs specs={[]} values={{}} onChange={jest.fn()} />,
  );
  expect(container).toBeEmptyDOMElement();
});

it("offers the pipeline's own allowed values as the options", () => {
  render(<SamplesheetInputs specs={[platform]} values={{}} onChange={jest.fn()} />);

  const field = screen.getByLabelText(/instrument platform/i);
  expect(field).toBeInTheDocument();
  ["ILLUMINA", "OXFORD_NANOPORE", "PACBIO_SMRT"].forEach((opt) =>
    expect(screen.getByRole("option", { name: opt })).toBeInTheDocument(),
  );
});

it("reports the chosen value under the launch parameter name", () => {
  const onChange = jest.fn();
  render(<SamplesheetInputs specs={[platform]} values={{}} onChange={onChange} />);

  fireEvent.change(screen.getByLabelText(/instrument platform/i), {
    target: { value: "ILLUMINA" },
  });

  expect(onChange).toHaveBeenCalledWith({ instrument_platform: "ILLUMINA" });
});

it("keeps values already chosen for other fields", () => {
  const onChange = jest.fn();
  render(
    <SamplesheetInputs
      specs={[platform, readStructure]}
      values={{ read_structure: "12M11S+T" }}
      onChange={onChange}
    />,
  );

  fireEvent.change(screen.getByLabelText(/instrument platform/i), {
    target: { value: "ILLUMINA" },
  });

  expect(onChange).toHaveBeenCalledWith({
    read_structure: "12M11S+T",
    instrument_platform: "ILLUMINA",
  });
});

it("renders a free-text field when the schema constrains nothing", () => {
  render(<SamplesheetInputs specs={[readStructure]} values={{}} onChange={jest.fn()} />);

  const field = screen.getByLabelText(/read structure/i);
  expect(field.tagName).toBe("INPUT");
});

it("shows the current value", () => {
  render(
    <SamplesheetInputs
      specs={[platform]}
      values={{ instrument_platform: "PACBIO_SMRT" }}
      onChange={jest.fn()}
    />,
  );

  expect(screen.getByLabelText(/instrument platform/i)).toHaveValue("PACBIO_SMRT");
});

it("marks a required field that has no value yet", () => {
  render(<SamplesheetInputs specs={[platform]} values={{}} onChange={jest.fn()} />);

  expect(screen.getByText("(required)")).toBeInTheDocument();
});

it("stops flagging the field once it is answered", () => {
  render(
    <SamplesheetInputs
      specs={[platform]}
      values={{ instrument_platform: "ILLUMINA" }}
      onChange={jest.fn()}
    />,
  );

  expect(screen.queryByText("(required)")).not.toBeInTheDocument();
});

it("explains why it is asking, in plain language", () => {
  render(<SamplesheetInputs specs={[platform]} values={{}} onChange={jest.fn()} />);

  // The user needs to know this is about their samples, not a tuning knob:
  // leaving it empty blocks the launch rather than falling back to a default.
  expect(screen.getByText(/this pipeline needs/i)).toBeInTheDocument();
});
