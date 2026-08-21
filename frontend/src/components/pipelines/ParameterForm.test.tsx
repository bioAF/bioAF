import { render, screen } from "@testing-library/react";
import { ParameterForm } from "./ParameterForm";
import type { ParameterSchema } from "@/lib/types";

/**
 * JSON Schema 2020-12 renamed `definitions` to `$defs`, and the current nf-core
 * template emits `$defs`. The form only read `definitions`, so 13 of the 20 most
 * popular pipelines fell back to a raw JSON textarea, including rnaseq, sarek
 * and scrnaseq. Every pipeline that adopts the current template joins them.
 */

const GROUP = {
  title: "Input/output options",
  properties: {
    genome: { type: "string", description: "Reference genome", enum: ["GRCh38", "GRCm39"] },
    save_reference: { type: "boolean", description: "Keep the index" },
  },
  required: ["genome"],
};

const legacySchema = { definitions: { input_output_options: GROUP } } as ParameterSchema;
const modernSchema = { $defs: { input_output_options: GROUP } } as ParameterSchema;

const noop = () => {};

it("renders fields from a $defs schema (current nf-core template)", () => {
  render(<ParameterForm schema={modernSchema} defaultParams={{}} values={{}} onChange={noop} />);

  expect(screen.getByText("Input/output options")).toBeInTheDocument();
  expect(screen.getByLabelText(/genome/i)).toBeInTheDocument();
});

it("does not fall back to the raw JSON box for a $defs schema", () => {
  render(<ParameterForm schema={modernSchema} defaultParams={{}} values={{}} onChange={noop} />);

  expect(screen.queryByText(/No parameter schema available/i)).not.toBeInTheDocument();
});

it("still renders a legacy definitions schema", () => {
  render(<ParameterForm schema={legacySchema} defaultParams={{}} values={{}} onChange={noop} />);

  expect(screen.getByText("Input/output options")).toBeInTheDocument();
  expect(screen.getByLabelText(/genome/i)).toBeInTheDocument();
});

it("offers the schema's enum values as options", () => {
  render(<ParameterForm schema={modernSchema} defaultParams={{}} values={{}} onChange={noop} />);

  expect(screen.getByRole("option", { name: "GRCh38" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "GRCm39" })).toBeInTheDocument();
});

it("falls back to the JSON box only when the pipeline really ships no schema", () => {
  render(<ParameterForm schema={null} defaultParams={{}} values={{}} onChange={noop} />);

  expect(screen.getByText(/No parameter schema available/i)).toBeInTheDocument();
});

it("treats an empty $defs the same as no schema", () => {
  render(<ParameterForm schema={{ $defs: {} } as ParameterSchema} defaultParams={{}} values={{}} onChange={noop} />);

  expect(screen.getByText(/No parameter schema available/i)).toBeInTheDocument();
});
