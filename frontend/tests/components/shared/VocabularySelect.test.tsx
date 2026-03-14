import { render, screen } from "@testing-library/react";
import { VocabularySelect } from "@/components/shared/VocabularySelect";

// Mock useVocabulary so the test is not coupled to the API
jest.mock("@/hooks/useVocabulary", () => ({
  useVocabulary: (fieldName: string) => {
    if (fieldName === "molecule_type") {
      return {
        values: [
          { id: 1, value: "total RNA", display_label: null, display_order: 1, is_default: true, is_active: true },
          { id: 2, value: "polyA RNA", display_label: "polyA RNA (enriched)", display_order: 2, is_default: false, is_active: true },
        ],
        loading: false,
      };
    }
    return { values: [], loading: false };
  },
}));

describe("VocabularySelect", () => {
  it("renders options using the value field from the API response", () => {
    render(
      <VocabularySelect
        fieldName="molecule_type"
        value={null}
        onChange={jest.fn()}
        placeholder="Molecule Type..."
      />
    );
    expect(screen.getByRole("option", { name: "total RNA" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "polyA RNA (enriched)" })).toBeInTheDocument();
  });

  it("uses display_label when present, falls back to value", () => {
    render(
      <VocabularySelect
        fieldName="molecule_type"
        value={null}
        onChange={jest.fn()}
      />
    );
    // display_label is null for total RNA — falls back to value
    expect(screen.getByRole("option", { name: "total RNA" })).toBeInTheDocument();
    // display_label is set for polyA RNA
    expect(screen.getByRole("option", { name: "polyA RNA (enriched)" })).toBeInTheDocument();
  });

  it("renders empty select for a field with no vocabulary values", () => {
    render(
      <VocabularySelect
        fieldName="unknown_field"
        value={null}
        onChange={jest.fn()}
        placeholder="Pick one..."
        allowEmpty
      />
    );
    // Only the empty/placeholder option
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("Pick one...");
  });
});
