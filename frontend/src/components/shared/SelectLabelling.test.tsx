/**
 * VocabularySelect and AssaySelect name themselves with aria-label when used
 * alone, and take a Field's id and description when a visible label exists:
 * an aria-label would override that label.
 */
import { render, screen } from "@testing-library/react";
import { Field } from "@/components/ui/Field";
import { VocabularySelect } from "./VocabularySelect";
import { AssaySelect } from "./AssaySelect";

jest.mock("@/hooks/useVocabulary", () => ({
  useVocabulary: () => ({ values: [{ id: 1, value: "rna", display_label: "RNA" }], loading: false }),
}));

test("alone, each select keeps its own name", () => {
  render(
    <>
      <VocabularySelect fieldName="molecule_type" value={null} onChange={() => {}} placeholder="Molecule Type..." />
      <AssaySelect value={null} onChange={() => {}} />
    </>,
  );
  expect(screen.getByLabelText("Molecule Type...")).toBeInTheDocument();
  expect(screen.getByLabelText("Assay")).toBeInTheDocument();
});

test("inside a Field, the visible label names them and the hint describes them", () => {
  render(
    <>
      <Field label="Molecule Type" hint="From the lab's vocabulary">
        {(control) => (
          <VocabularySelect {...control} fieldName="molecule_type" value={null} onChange={() => {}} placeholder="Molecule Type..." />
        )}
      </Field>
      <Field label="Assay used">{(control) => <AssaySelect {...control} value={null} onChange={() => {}} />}</Field>
    </>,
  );
  const molecule = screen.getByLabelText("Molecule Type");
  expect(molecule.tagName).toBe("SELECT");
  expect(molecule).toHaveAccessibleDescription("From the lab's vocabulary");
  expect(screen.getByLabelText("Assay used").tagName).toBe("SELECT");
});

test("a Field error marks these selects invalid, as it does an Input", () => {
  render(
    <>
      <Field label="Molecule Type" error="Pick a molecule type">
        {(control) => (
          <VocabularySelect {...control} fieldName="molecule_type" value={null} onChange={() => {}} placeholder="Molecule Type..." />
        )}
      </Field>
      <Field label="Assay used" error="Pick an assay">
        {(control) => <AssaySelect {...control} value={null} onChange={() => {}} />}
      </Field>
      <Field label="Library Layout">
        {(control) => <VocabularySelect {...control} fieldName="library_layout" value={null} onChange={() => {}} />}
      </Field>
    </>,
  );
  const molecule = screen.getByLabelText("Molecule Type");
  expect(molecule).toHaveAttribute("aria-invalid", "true");
  expect(molecule).toHaveAccessibleDescription("Pick a molecule type");
  expect(screen.getByLabelText("Assay used")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByLabelText("Assay used")).toHaveAccessibleDescription("Pick an assay");
  expect(screen.getByLabelText("Library Layout")).not.toHaveAttribute("aria-invalid");
});
