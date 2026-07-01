import { render, screen, fireEvent } from "@testing-library/react";
import { AssaySelect } from "./AssaySelect";

describe("AssaySelect", () => {
  it("renders the controlled assay vocabulary as options with friendly labels", () => {
    render(<AssaySelect value={null} onChange={() => {}} />);
    const select = screen.getByLabelText("Assay") as HTMLSelectElement;
    // Placeholder first, then the three system-vocab values in order.
    expect(Array.from(select.options).map((o) => o.value)).toEqual([
      "",
      "bulk_rna",
      "scrna",
      "other",
    ]);
    expect(
      screen.getByRole("option", { name: "Bulk RNA-seq" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Single-cell RNA-seq" }),
    ).toBeInTheDocument();
  });

  it("reflects the current value", () => {
    render(<AssaySelect value="scrna" onChange={() => {}} />);
    expect((screen.getByLabelText("Assay") as HTMLSelectElement).value).toBe(
      "scrna",
    );
  });

  it("calls onChange with the selected assay value", () => {
    const onChange = jest.fn();
    render(<AssaySelect value={null} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Assay"), {
      target: { value: "bulk_rna" },
    });
    expect(onChange).toHaveBeenCalledWith("bulk_rna");
  });

  it("calls onChange with null when cleared back to the placeholder", () => {
    const onChange = jest.fn();
    render(<AssaySelect value="other" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Assay"), { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith(null);
  });
});
